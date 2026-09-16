from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id                 INTEGER PRIMARY KEY,
    timezone                TEXT NOT NULL DEFAULT '',
    current_conversation_id INTEGER,
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    title      TEXT NOT NULL DEFAULT 'New chat',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_chat ON conversations (chat_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    conversation_id INTEGER,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
-- idx_messages_conv bu yerda emas — messages eski bazada allaqachon mavjud
-- bo'lishi mumkin va conversation_id ustuni faqat _migrate() da qo'shiladi.
-- Indeks shu ustun kafolatlangandan keyin, _migrate() oxirida yaratiladi.

CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    fact       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    due_at     TEXT NOT NULL,
    repeat     TEXT NOT NULL DEFAULT 'none',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_active ON reminders (active, due_at);
"""

DEFAULT_TITLE = "New chat"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() chaqirilmagan")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _commit(self) -> None:
        await self.conn.commit()

    async def _columns(self, table: str) -> set[str]:
        async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
        return {row["name"] for row in rows}

    async def _migrate(self) -> None:
        """Eski bazalarni (suhbatlar tizimidan oldingi) yangi sxemaga o'tkazadi."""
        if "current_conversation_id" not in await self._columns("users"):
            await self.conn.execute("ALTER TABLE users ADD COLUMN current_conversation_id INTEGER")

        if "conversation_id" not in await self._columns("messages"):
            await self.conn.execute("ALTER TABLE messages ADD COLUMN conversation_id INTEGER")
            async with self.conn.execute(
                "SELECT DISTINCT chat_id FROM messages WHERE conversation_id IS NULL"
            ) as cur:
                chat_ids = [row["chat_id"] for row in await cur.fetchall()]
            for chat_id in chat_ids:
                conv_id = await self.new_conversation(chat_id, title="Old chat")
                await self.conn.execute(
                    "UPDATE messages SET conversation_id = ? WHERE chat_id = ? AND conversation_id IS NULL",
                    (conv_id, chat_id),
                )

        # conversation_id ustuni endi kafolatlangan (yangi bazada ham, mana
        # shu yerda migratsiya qilingan eskisida ham) — indeksni shu yerda yaratamiz.
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conversation_id, id)"
        )
        await self._commit()

    # --- foydalanuvchi / vaqt mintaqasi -------------------------------------

    async def get_timezone(self, chat_id: int, default: str) -> str:
        async with self.conn.execute(
            "SELECT timezone FROM users WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        return (row["timezone"] if row else "") or default

    async def set_timezone(self, chat_id: int, tz: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO users (chat_id, timezone, created_at) VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET timezone = excluded.timezone
            """,
            (chat_id, tz, _now()),
        )
        await self._commit()

    # --- suhbatlar (mavzu bo'yicha) -----------------------------------------

    async def new_conversation(self, chat_id: int, title: str = DEFAULT_TITLE) -> int:
        now = _now()
        cur = await self.conn.execute(
            "INSERT INTO conversations (chat_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (chat_id, title, now, now),
        )
        conversation_id = int(cur.lastrowid)
        await self.conn.execute(
            """
            INSERT INTO users (chat_id, timezone, current_conversation_id, created_at)
            VALUES (?, '', ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET current_conversation_id = excluded.current_conversation_id
            """,
            (chat_id, conversation_id, now),
        )
        await self._commit()
        return conversation_id

    async def current_conversation(self, chat_id: int) -> int:
        async with self.conn.execute(
            "SELECT current_conversation_id FROM users WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        conversation_id = row["current_conversation_id"] if row else None
        if conversation_id is not None:
            async with self.conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND chat_id = ?",
                (conversation_id, chat_id),
            ) as cur:
                if await cur.fetchone() is not None:
                    return conversation_id
        return await self.new_conversation(chat_id)

    async def switch_conversation(self, chat_id: int, conversation_id: int) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND chat_id = ?",
            (conversation_id, chat_id),
        ) as cur:
            if await cur.fetchone() is None:
                return False
        await self.conn.execute(
            """
            INSERT INTO users (chat_id, timezone, current_conversation_id, created_at)
            VALUES (?, '', ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET current_conversation_id = excluded.current_conversation_id
            """,
            (chat_id, conversation_id, _now()),
        )
        await self._commit()
        return True

    async def list_conversations(self, chat_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT id, title, updated_at FROM conversations WHERE chat_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (chat_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    async def set_conversation_title(self, conversation_id: int, title: str) -> None:
        # Faqat hali sarlavha qo'yilmagan (standart) suhbatlarga tegadi —
        # foydalanuvchi keyin o'zgartirsa qayta yozib yubormaslik uchun.
        await self.conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ? AND title = ?",
            (title, conversation_id, DEFAULT_TITLE),
        )
        await self._commit()

    # --- suhbat tarixi (bitta mavzu ichida) ---------------------------------

    async def add_message(self, chat_id: int, conversation_id: int, message: dict[str, Any]) -> None:
        now = _now()
        await self.conn.execute(
            "INSERT INTO messages (chat_id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, conversation_id, message["role"], json.dumps(message, ensure_ascii=False), now),
        )
        await self.conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
        )
        await self._commit()

    async def load_history(self, conversation_id: int, limit: int) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT content FROM (SELECT id, content FROM messages "
            "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (conversation_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [json.loads(row["content"]) for row in rows]

    # --- xotira (foydalanuvchi haqida, suhbatdan mustaqil) ------------------

    async def add_memory(self, chat_id: int, fact: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO memories (chat_id, fact, created_at) VALUES (?, ?, ?)",
            (chat_id, fact, _now()),
        )
        await self._commit()
        return int(cur.lastrowid)

    async def list_memories(self, chat_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT id, fact FROM memories WHERE chat_id = ? ORDER BY id", (chat_id,)
        ) as cur:
            return list(await cur.fetchall())

    async def delete_memory(self, chat_id: int, memory_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM memories WHERE chat_id = ? AND id = ?", (chat_id, memory_id)
        )
        await self._commit()
        return cur.rowcount > 0

    # --- vazifalar (suhbatdan mustaqil) -------------------------------------

    async def add_todo(self, chat_id: int, text: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO todos (chat_id, text, created_at) VALUES (?, ?, ?)",
            (chat_id, text, _now()),
        )
        await self._commit()
        return int(cur.lastrowid)

    async def list_todos(self, chat_id: int, include_done: bool) -> list[aiosqlite.Row]:
        query = "SELECT id, text, done FROM todos WHERE chat_id = ?"
        if not include_done:
            query += " AND done = 0"
        async with self.conn.execute(query + " ORDER BY id", (chat_id,)) as cur:
            return list(await cur.fetchall())

    async def set_todo_done(self, chat_id: int, todo_id: int, done: bool) -> bool:
        cur = await self.conn.execute(
            "UPDATE todos SET done = ? WHERE chat_id = ? AND id = ?",
            (1 if done else 0, chat_id, todo_id),
        )
        await self._commit()
        return cur.rowcount > 0

    async def delete_todo(self, chat_id: int, todo_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM todos WHERE chat_id = ? AND id = ?", (chat_id, todo_id)
        )
        await self._commit()
        return cur.rowcount > 0

    # --- eslatmalar (suhbatdan mustaqil) ------------------------------------

    async def add_reminder(self, chat_id: int, text: str, due_at: str, repeat: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO reminders (chat_id, text, due_at, repeat, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, text, due_at, repeat, _now()),
        )
        await self._commit()
        return int(cur.lastrowid)

    async def list_reminders(self, chat_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT id, text, due_at, repeat FROM reminders "
            "WHERE chat_id = ? AND active = 1 ORDER BY due_at",
            (chat_id,),
        ) as cur:
            return list(await cur.fetchall())

    async def all_active_reminders(self) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT id, chat_id, text, due_at, repeat FROM reminders WHERE active = 1"
        ) as cur:
            return list(await cur.fetchall())

    async def deactivate_reminder(self, reminder_id: int, chat_id: int | None = None) -> bool:
        query = "UPDATE reminders SET active = 0 WHERE id = ?"
        params: tuple[Any, ...] = (reminder_id,)
        if chat_id is not None:
            query += " AND chat_id = ?"
            params += (chat_id,)
        cur = await self.conn.execute(query, params)
        await self._commit()
        return cur.rowcount > 0

    async def update_reminder_due(self, reminder_id: int, due_at: str) -> None:
        await self.conn.execute(
            "UPDATE reminders SET due_at = ? WHERE id = ?", (due_at, reminder_id)
        )
        await self._commit()
