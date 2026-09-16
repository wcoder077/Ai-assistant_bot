from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import agent, config, files, reminders
from .db import Database
from .formatting import split_message, to_telegram_html
from .tools import REPEAT_LABELS, WEEKDAYS

log = logging.getLogger(__name__)

BTN_REMINDERS = "⏰ Reminders"
BTN_TODOS = "✅ Tasks"
BTN_MEMORY = "🧠 About me"
BTN_CHATS = "💬 Chats"
BTN_NEW_CHAT = "🆕 New chat"
BTN_HELP = "ℹ️ Help"

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(BTN_REMINDERS), KeyboardButton(BTN_TODOS)],
        [KeyboardButton(BTN_MEMORY), KeyboardButton(BTN_CHATS)],
        [KeyboardButton(BTN_NEW_CHAT), KeyboardButton(BTN_HELP)],
    ],
    resize_keyboard=True,
)

HELP_TEXT = """I'm your personal assistant. Just write to me in plain language.

What I can do:
• Chat, answer questions, write text and translate
• Set reminders — "remind me to see the doctor at 9 tomorrow" or "wake me up at 7 every day"
• Task list — "add buying bread to my list", "show my tasks"
• Search the web — news, weather, prices
• Read files — PDF, Word, Excel, text files and images

Buttons below:
⏰ Reminders — your pending reminders, with a cancel button
✅ Tasks — your list, with done/delete buttons
🧠 About me — the facts I've remembered about you
💬 Chats — go back to earlier topics
🆕 New chat — start a different topic (the old one is kept)

Commands:
/start — start
/new — start a new chat
/chats — list of chats
/reminders — list of reminders
/tasks — list of tasks
/memory — what I remember about you
/help — this help message
/id — your Telegram ID

Your reminders, tasks and memory are independent of the chat — they never get
deleted even if you tap "New chat" or the AI model behind the bot is swapped."""


def _authorized(update: Update) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    user = update.effective_user
    return user is not None and user.id in config.ALLOWED_USER_IDS


async def _send(update: Update, text: str) -> None:
    message = update.effective_message
    for chunk in split_message(text):
        try:
            await message.reply_text(to_telegram_html(chunk), parse_mode="HTML")
        except BadRequest:
            log.warning("HTML formatting failed, sending plain text")
            await message.reply_text(chunk)


async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    while True:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            return
        await asyncio.sleep(4)


def _reminder_when(row, zone: ZoneInfo) -> str:
    local = datetime.fromisoformat(row["due_at"]).astimezone(zone)
    if row["repeat"] == "daily":
        return f"daily at {local:%H:%M}"
    if row["repeat"] == "weekly":
        return f"every {WEEKDAYS[local.weekday()]} at {local:%H:%M}"
    return f"{local:%Y-%m-%d %H:%M}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    user = update.effective_user
    name = (user.first_name if user else None) or "there"
    await update.effective_message.reply_text(f"Hi, {name}!\n\n{HELP_TEXT}", reply_markup=MAIN_KB)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=MAIN_KB)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, f"Telegram ID: `{_user_id(update)}`")


async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    await db.new_conversation(update.effective_chat.id)
    await update.effective_message.reply_text(
        "🆕 New chat started. Your previous chat is saved — you can go back to it "
        f"anytime via {BTN_CHATS}.",
        reply_markup=MAIN_KB,
    )


async def chats_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    rows = await db.list_conversations(update.effective_chat.id, limit=10)
    if not rows:
        await update.effective_message.reply_text("No chats yet.")
        return
    buttons = [
        [InlineKeyboardButton(row["title"] or "New chat", callback_data=f"conv:{row['id']}")]
        for row in rows
    ]
    await update.effective_message.reply_text(
        "💬 Your recent chats — pick one:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def reminders_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    chat_id = update.effective_chat.id
    tz = await db.get_timezone(chat_id, config.DEFAULT_TIMEZONE)
    zone = ZoneInfo(tz)
    rows = await db.list_reminders(chat_id)
    if not rows:
        await update.effective_message.reply_text("No pending reminders.")
        return
    lines = ["<b>⏰ Reminders</b>"]
    buttons = []
    for row in rows:
        when = _reminder_when(row, zone)
        lines.append(f"#{row['id']} — {row['text']} — {when} ({REPEAT_LABELS[row['repeat']]})")
        buttons.append([InlineKeyboardButton(f"🗑 Cancel #{row['id']}", callback_data=f"remdel:{row['id']}")])
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def todos_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    rows = await db.list_todos(update.effective_chat.id, include_done=False)
    if not rows:
        await update.effective_message.reply_text("Task list is empty.")
        return
    lines = ["<b>✅ Tasks</b>"]
    buttons = []
    for row in rows:
        lines.append(f"#{row['id']} — {row['text']}")
        buttons.append([
            InlineKeyboardButton(f"✔ Done #{row['id']}", callback_data=f"tododone:{row['id']}"),
            InlineKeyboardButton(f"🗑 Delete #{row['id']}", callback_data=f"tododel:{row['id']}"),
        ])
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def memory_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    rows = await db.list_memories(update.effective_chat.id)
    if not rows:
        await update.effective_message.reply_text("I haven't remembered anything about you yet.")
        return
    lines = ["<b>🧠 What I remember about you</b>"]
    buttons = []
    for row in rows:
        lines.append(f"#{row['id']} — {row['fact']}")
        buttons.append([InlineKeyboardButton(f"🗑 Delete #{row['id']}", callback_data=f"memdel:{row['id']}")])
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data or query.message is None:
        return
    db: Database = context.bot_data["db"]
    chat_id = query.message.chat_id
    action, _, raw_id = query.data.partition(":")

    if action == "remdel":
        reminder_id = int(raw_id)
        ok = await db.deactivate_reminder(reminder_id, chat_id)
        if ok:
            reminders.cancel_job(context.job_queue, reminder_id)
        await query.answer("Cancelled ✔" if ok else "Not found")
        if ok:
            await query.edit_message_reply_markup(reply_markup=None)

    elif action == "tododone":
        ok = await db.set_todo_done(chat_id, int(raw_id), True)
        await query.answer("Done ✔" if ok else "Not found")
        if ok:
            await query.edit_message_reply_markup(reply_markup=None)

    elif action == "tododel":
        ok = await db.delete_todo(chat_id, int(raw_id))
        await query.answer("Deleted" if ok else "Not found")
        if ok:
            await query.edit_message_reply_markup(reply_markup=None)

    elif action == "memdel":
        ok = await db.delete_memory(chat_id, int(raw_id))
        await query.answer("Deleted" if ok else "Not found")
        if ok:
            await query.edit_message_reply_markup(reply_markup=None)

    elif action == "conv":
        ok = await db.switch_conversation(chat_id, int(raw_id))
        await query.answer("Switched ✔" if ok else "Not found")
        if ok:
            await query.edit_message_text(f"✅ Switched to «{query.message.text}».")

    else:
        await query.answer()


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Legacy /reset — now behaves the same as "New chat".
    await new_chat(update, context)


def _user_id(update: Update) -> int | str:
    return update.effective_user.id if update.effective_user else "unknown"


async def _deny(update: Update) -> None:
    await update.effective_message.reply_text(
        "This is a private bot. The owner needs to grant you access.\n"
        f"Telegram ID: {_user_id(update)}"
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        log.info("Unauthorized user: %s", _user_id(update))
        await _deny(update)
        return

    message = update.effective_message
    chat_id = update.effective_chat.id
    db: Database = context.bot_data["db"]

    blocks, notes = await files.collect_blocks(message, context.bot)
    for note in notes:
        await message.reply_text(note)
    if not blocks:
        return

    conversation_id = await db.current_conversation(chat_id)
    title_source = message.text or message.caption
    if title_source:
        await db.set_conversation_title(conversation_id, title_source.strip()[:40])

    typing = asyncio.create_task(_keep_typing(context, chat_id))
    try:
        tz = await db.get_timezone(chat_id, config.DEFAULT_TIMEZONE)
        reply = await agent.respond(db, context.job_queue, chat_id, conversation_id, blocks, tz)
        await _send(update, reply)
    except agent.AllProvidersFailed as error:
        log.error("No provider responded: %s", error)
        await message.reply_text(
            "No AI provider could respond:\n"
            f"{error.summary()}\n\n"
            "If the balance ran out, add a free provider key to .env "
            "(GEMINI_API_KEY — aistudio.google.com) and restart the bot."
        )
    finally:
        typing.cancel()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unexpected error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "An unexpected error occurred. Please try again."
            )
        except Exception:
            pass


async def _startup(application: Application) -> None:
    db = Database(config.DB_PATH)
    await db.connect()
    application.bot_data["db"] = db
    restored = await reminders.restore_all(
        application.job_queue, db, config.DEFAULT_TIMEZONE
    )
    log.info("Database ready, %s reminder(s) restored", restored)
    log.info(
        "Provider queue: %s",
        " -> ".join(f"{p.name} ({p.model})" for p in config.PROVIDERS),
    )
    if not config.ALLOWED_USER_IDS:
        log.warning(
            "ALLOWED_USER_IDS is empty — anyone who finds the bot can use it, "
            "and API costs will be on you. Set your own ID in .env."
        )
    await application.bot.set_my_commands([
        ("start", "Start the bot"),
        ("new", "Start a new chat"),
        ("chats", "List of chats"),
        ("reminders", "List of reminders"),
        ("tasks", "List of tasks"),
        ("memory", "What I remember about you"),
        ("help", "Help"),
        ("id", "Your Telegram ID"),
    ])


async def _shutdown(application: Application) -> None:
    db: Database | None = application.bot_data.get("db")
    if db is not None:
        await db.close()


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s — %(message)s", level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_startup)
        .post_shutdown(_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("new", new_chat))
    application.add_handler(CommandHandler("chats", chats_button))
    application.add_handler(CommandHandler("reminders", reminders_button))
    application.add_handler(CommandHandler("tasks", todos_button))
    application.add_handler(CommandHandler("memory", memory_button))
    application.add_handler(CommandHandler("id", whoami))

    application.add_handler(MessageHandler(filters.Text([BTN_REMINDERS]), reminders_button))
    application.add_handler(MessageHandler(filters.Text([BTN_TODOS]), todos_button))
    application.add_handler(MessageHandler(filters.Text([BTN_MEMORY]), memory_button))
    application.add_handler(MessageHandler(filters.Text([BTN_CHATS]), chats_button))
    application.add_handler(MessageHandler(filters.Text([BTN_NEW_CHAT]), new_chat))
    application.add_handler(MessageHandler(filters.Text([BTN_HELP]), help_command))

    application.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND)
            | filters.PHOTO
            | filters.Document.ALL
            | filters.VOICE
            | filters.AUDIO
            | filters.VIDEO
            | filters.VIDEO_NOTE,
            on_message,
        )
    )
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_error_handler(on_error)

    log.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
