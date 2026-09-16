from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes, JobQueue

from .db import Database
from .formatting import escape_html

log = logging.getLogger(__name__)

REPEATS = ("none", "daily", "weekly")


def _job_name(reminder_id: int) -> str:
    return f"reminder:{reminder_id}"


def cancel_job(job_queue: JobQueue, reminder_id: int) -> None:
    for job in job_queue.get_jobs_by_name(_job_name(reminder_id)):
        job.schedule_removal()


async def _deliver(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    data = job.data
    text = f"⏰ <b>Reminder:</b> {data['html_text']}"
    if data.get("late"):
        text += "\n<i>(delayed because the bot was offline)</i>"
    await context.bot.send_message(chat_id=job.chat_id, text=text, parse_mode="HTML")

    if data["repeat"] == "none":
        db: Database = context.bot_data["db"]
        await db.deactivate_reminder(data["reminder_id"])


def schedule(
    job_queue: JobQueue,
    reminder_id: int,
    chat_id: int,
    text: str,
    due_at: datetime,
    repeat: str,
    tz: str,
) -> None:
    """due_at UTC bo'lishi kerak; takrorlanuvchilar uchun birinchi vaqt sifatida ishlatiladi."""
    cancel_job(job_queue, reminder_id)
    data = {
        "reminder_id": reminder_id,
        "chat_id": chat_id,
        "html_text": escape_html(text),
        "repeat": repeat,
    }
    name = _job_name(reminder_id)

    if repeat == "none":
        now = datetime.now(timezone.utc)
        if due_at <= now:
            data["late"] = True
            due_at = now + timedelta(seconds=5)
        job_queue.run_once(_deliver, when=due_at, data=data, name=name, chat_id=chat_id)
        return

    local_time = due_at.astimezone(ZoneInfo(tz)).timetz()
    if repeat == "weekly":
        # PTB'da 0-6 yakshanbadan shanbagacha, Python'da 0 = dushanba.
        ptb_day = (due_at.astimezone(ZoneInfo(tz)).weekday() + 1) % 7
        job_queue.run_daily(
            _deliver, time=local_time, days=(ptb_day,), data=data, name=name, chat_id=chat_id
        )
    else:
        job_queue.run_daily(_deliver, time=local_time, data=data, name=name, chat_id=chat_id)


async def restore_all(job_queue: JobQueue, db: Database, default_tz: str) -> int:
    """Bot qayta ishga tushganda saqlangan eslatmalarni qayta rejalashtiradi."""
    restored = 0
    for row in await db.all_active_reminders():
        try:
            due_at = datetime.fromisoformat(row["due_at"])
            tz = await db.get_timezone(row["chat_id"], default_tz)
            schedule(
                job_queue,
                row["id"],
                row["chat_id"],
                row["text"],
                due_at,
                row["repeat"],
                tz,
            )
            restored += 1
        except Exception:
            log.exception("Eslatmani tiklab bo'lmadi: id=%s", row["id"])
    return restored
