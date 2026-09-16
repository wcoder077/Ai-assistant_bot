from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ddgs import DDGS
from telegram.ext import JobQueue

from . import config, reminders
from .db import Database

log = logging.getLogger(__name__)

REPEAT_LABELS = {"none": "once", "daily": "daily", "weekly": "weekly"}
WEEKDAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


@dataclass
class ToolContext:
    db: Database
    job_queue: JobQueue
    chat_id: int
    tz: str


_TOOLS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": (
            "Internetdan qidiradi. Yangi yoki o'zgaruvchan ma'lumot kerak bo'lganda "
            "ishlating: yangiliklar, ob-havo, narxlar, kurslar, '2026 yilda...' kabi "
            "savollar. Natijada sarlavha, havola va qisqacha matn qaytadi."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Qidiruv so'rovi."},
                "max_results": {
                    "type": "integer",
                    "description": "Natijalar soni (1-10). Standart 5.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_reminder",
        "description": (
            "Belgilangan vaqtda foydalanuvchiga Telegram orqali eslatma yuborish uchun "
            "yangi eslatma yaratadi. Vaqtni foydalanuvchining mahalliy vaqtida bering."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Eslatma matni."},
                "when": {
                    "type": "string",
                    "description": (
                        "Mahalliy vaqt, ISO formatda: 'YYYY-MM-DDTHH:MM' "
                        "(masalan 2026-09-16T09:00). Takrorlanuvchi eslatma uchun "
                        "birinchi ishga tushish vaqti."
                    ),
                },
                "repeat": {
                    "type": "string",
                    "enum": ["none", "daily", "weekly"],
                    "description": "Takrorlanish turi. Ko'rsatilmasa 'none'.",
                },
            },
            "required": ["text", "when"],
        },
    },
    {
        "name": "list_reminders",
        "description": "Foydalanuvchining kutilayotgan barcha eslatmalarini qaytaradi.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_reminder",
        "description": "Eslatmani ID bo'yicha bekor qiladi.",
        "parameters": {
            "type": "object",
            "properties": {"reminder_id": {"type": "integer"}},
            "required": ["reminder_id"],
        },
    },
    {
        "name": "add_todo",
        "description": "Foydalanuvchining vazifalar ro'yxatiga yangi vazifa qo'shadi.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "list_todos",
        "description": "Vazifalar ro'yxatini qaytaradi.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_done": {
                    "type": "boolean",
                    "description": "Bajarilganlarini ham ko'rsatish. Standart: false.",
                }
            },
        },
    },
    {
        "name": "complete_todo",
        "description": "Vazifani bajarilgan deb belgilaydi.",
        "parameters": {
            "type": "object",
            "properties": {"todo_id": {"type": "integer"}},
            "required": ["todo_id"],
        },
    },
    {
        "name": "delete_todo",
        "description": "Vazifani ro'yxatdan o'chiradi.",
        "parameters": {
            "type": "object",
            "properties": {"todo_id": {"type": "integer"}},
            "required": ["todo_id"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Foydalanuvchi haqidagi uzoq muddatli faktni eslab qoladi (ismi, ishi, "
            "yoqtirishlari, muhim sanalar). Keyingi suhbatlarda ham eslab qolinadi. "
            "Faqat haqiqatan foydali, doimiy ma'lumotlarni saqlang."
        ),
        "parameters": {
            "type": "object",
            "properties": {"fact": {"type": "string"}},
            "required": ["fact"],
        },
    },
    {
        "name": "forget",
        "description": "Eslab qolingan faktni ID bo'yicha o'chiradi.",
        "parameters": {
            "type": "object",
            "properties": {"memory_id": {"type": "integer"}},
            "required": ["memory_id"],
        },
    },
    {
        "name": "set_timezone",
        "description": (
            "Foydalanuvchining vaqt mintaqasini o'rnatadi, masalan 'Asia/Tashkent' "
            "yoki 'Europe/Moscow'."
        ),
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string"}},
            "required": ["timezone"],
        },
    },
]


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "function": spec} for spec in _TOOLS
]


async def _web_search(ctx: ToolContext, args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Xato: qidiruv so'rovi bo'sh."

    count = max(1, min(int(args.get("max_results") or config.SEARCH_RESULTS), 10))
    results = await asyncio.to_thread(lambda: DDGS().text(query, max_results=count))
    if not results:
        return f"'{query}' bo'yicha hech narsa topilmadi."

    return "\n\n".join(
        f"{index}. {item.get('title', '')}\n{item.get('href', '')}\n{item.get('body', '')}"
        for index, item in enumerate(results, 1)
    )


async def _create_reminder(ctx: ToolContext, args: dict[str, Any]) -> str:
    text = str(args.get("text", "")).strip()
    if not text:
        return "Xato: eslatma matni bo'sh."

    repeat = str(args.get("repeat") or "none").lower()
    if repeat not in reminders.REPEATS:
        return f"Xato: repeat faqat {', '.join(reminders.REPEATS)} bo'lishi mumkin."

    try:
        parsed = datetime.fromisoformat(str(args.get("when", "")).strip())
    except ValueError:
        return "Xato: 'when' ISO formatda bo'lsin, masalan 2026-09-16T09:00."

    zone = ZoneInfo(ctx.tz)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    due_utc = parsed.astimezone(timezone.utc)

    if repeat == "none" and due_utc <= datetime.now(timezone.utc):
        return "Xato: bu vaqt allaqachon o'tib ketgan. Kelajakdagi vaqtni tanlang."

    reminder_id = await ctx.db.add_reminder(ctx.chat_id, text, due_utc.isoformat(), repeat)
    reminders.schedule(
        ctx.job_queue, reminder_id, ctx.chat_id, text, due_utc, repeat, ctx.tz
    )
    local = due_utc.astimezone(zone)
    return (
        f"Eslatma #{reminder_id} yaratildi: '{text}' — "
        f"{local:%Y-%m-%d %H:%M} ({REPEAT_LABELS[repeat]})."
    )


async def _list_reminders(ctx: ToolContext, args: dict[str, Any]) -> str:
    rows = await ctx.db.list_reminders(ctx.chat_id)
    if not rows:
        return "Kutilayotgan eslatma yo'q."
    zone = ZoneInfo(ctx.tz)
    lines = []
    for row in rows:
        local = datetime.fromisoformat(row["due_at"]).astimezone(zone)
        if row["repeat"] == "daily":
            when = f"har kuni {local:%H:%M}"
        elif row["repeat"] == "weekly":
            when = f"har {WEEKDAYS[local.weekday()]} {local:%H:%M}"
        else:
            when = f"{local:%Y-%m-%d %H:%M}"
        lines.append(f"#{row['id']} — {row['text']} — {when}")
    return "\n".join(lines)


async def _cancel_reminder(ctx: ToolContext, args: dict[str, Any]) -> str:
    reminder_id = int(args["reminder_id"])
    if not await ctx.db.deactivate_reminder(reminder_id, ctx.chat_id):
        return f"Eslatma #{reminder_id} topilmadi."
    reminders.cancel_job(ctx.job_queue, reminder_id)
    return f"Eslatma #{reminder_id} bekor qilindi."


async def _add_todo(ctx: ToolContext, args: dict[str, Any]) -> str:
    text = str(args.get("text", "")).strip()
    if not text:
        return "Xato: vazifa matni bo'sh."
    todo_id = await ctx.db.add_todo(ctx.chat_id, text)
    return f"Vazifa #{todo_id} qo'shildi: {text}"


async def _list_todos(ctx: ToolContext, args: dict[str, Any]) -> str:
    rows = await ctx.db.list_todos(ctx.chat_id, bool(args.get("include_done")))
    if not rows:
        return "Vazifalar ro'yxati bo'sh."
    return "\n".join(
        f"#{row['id']} [{'x' if row['done'] else ' '}] {row['text']}" for row in rows
    )


async def _complete_todo(ctx: ToolContext, args: dict[str, Any]) -> str:
    todo_id = int(args["todo_id"])
    if not await ctx.db.set_todo_done(ctx.chat_id, todo_id, True):
        return f"Vazifa #{todo_id} topilmadi."
    return f"Vazifa #{todo_id} bajarildi deb belgilandi."


async def _delete_todo(ctx: ToolContext, args: dict[str, Any]) -> str:
    todo_id = int(args["todo_id"])
    if not await ctx.db.delete_todo(ctx.chat_id, todo_id):
        return f"Vazifa #{todo_id} topilmadi."
    return f"Vazifa #{todo_id} o'chirildi."


async def _remember(ctx: ToolContext, args: dict[str, Any]) -> str:
    fact = str(args.get("fact", "")).strip()
    if not fact:
        return "Xato: fakt bo'sh."
    memory_id = await ctx.db.add_memory(ctx.chat_id, fact)
    return f"Eslab qoldim (#{memory_id}): {fact}"


async def _forget(ctx: ToolContext, args: dict[str, Any]) -> str:
    memory_id = int(args["memory_id"])
    if not await ctx.db.delete_memory(ctx.chat_id, memory_id):
        return f"Xotira #{memory_id} topilmadi."
    return f"Xotira #{memory_id} o'chirildi."


async def _set_timezone(ctx: ToolContext, args: dict[str, Any]) -> str:
    name = str(args.get("timezone", "")).strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return f"Xato: '{name}' noto'g'ri vaqt mintaqasi. Masalan: Asia/Tashkent."

    await ctx.db.set_timezone(ctx.chat_id, name)
    ctx.tz = name
    for row in await ctx.db.list_reminders(ctx.chat_id):
        reminders.schedule(
            ctx.job_queue,
            row["id"],
            ctx.chat_id,
            row["text"],
            datetime.fromisoformat(row["due_at"]),
            row["repeat"],
            name,
        )
    return f"Vaqt mintaqasi '{name}' qilib o'rnatildi."


HANDLERS: dict[str, Callable[[ToolContext, dict[str, Any]], Awaitable[str]]] = {
    "web_search": _web_search,
    "create_reminder": _create_reminder,
    "list_reminders": _list_reminders,
    "cancel_reminder": _cancel_reminder,
    "add_todo": _add_todo,
    "list_todos": _list_todos,
    "complete_todo": _complete_todo,
    "delete_todo": _delete_todo,
    "remember": _remember,
    "forget": _forget,
    "set_timezone": _set_timezone,
}


async def execute_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Noma'lum vosita: {name}", True
    try:
        return await handler(ctx, args), False
    except Exception as error:
        log.exception("Vosita xatosi: %s", name)
        return f"Vositani bajarishda xato: {error}", True
