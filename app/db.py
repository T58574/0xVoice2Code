import json
import logging
from datetime import datetime, timedelta

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = "gex.db"
_connection: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _connection
    if _connection is None:
        _connection = await aiosqlite.connect(DB_PATH)
        _connection.row_factory = aiosqlite.Row
        await _connection.execute("PRAGMA journal_mode=WAL")
        await _connection.execute("PRAGMA foreign_keys=ON")
    return _connection


async def close_db() -> None:
    global _connection
    if _connection:
        await _connection.close()
        _connection = None


async def init_db(db_path: str = "gex.db") -> None:
    global DB_PATH
    DB_PATH = db_path
    db = await get_db()

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            raw_text TEXT NOT NULL,
            formatted_text TEXT,
            category TEXT,
            tags TEXT,
            priority TEXT,
            summary TEXT,
            action_items TEXT,
            sentiment TEXT,
            duration REAL,
            source TEXT DEFAULT 'voice',
            mode TEXT DEFAULT 'dictation',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_transcriptions_user
            ON transcriptions(user_id);
        CREATE INDEX IF NOT EXISTS idx_transcriptions_created
            ON transcriptions(created_at);
        CREATE INDEX IF NOT EXISTS idx_transcriptions_category
            ON transcriptions(category);

        CREATE VIRTUAL TABLE IF NOT EXISTS transcriptions_fts USING fts5(
            raw_text,
            formatted_text,
            content='transcriptions',
            content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS trg_fts_insert AFTER INSERT ON transcriptions
        BEGIN
            INSERT INTO transcriptions_fts(rowid, raw_text, formatted_text)
            VALUES (new.id, new.raw_text, new.formatted_text);
        END;

        CREATE TRIGGER IF NOT EXISTS trg_fts_delete AFTER DELETE ON transcriptions
        BEGIN
            INSERT INTO transcriptions_fts(transcriptions_fts, rowid, raw_text, formatted_text)
            VALUES ('delete', old.id, old.raw_text, old.formatted_text);
        END;

        CREATE TRIGGER IF NOT EXISTS trg_fts_update AFTER UPDATE ON transcriptions
        BEGIN
            INSERT INTO transcriptions_fts(transcriptions_fts, rowid, raw_text, formatted_text)
            VALUES ('delete', old.id, old.raw_text, old.formatted_text);
            INSERT INTO transcriptions_fts(rowid, raw_text, formatted_text)
            VALUES (new.id, new.raw_text, new.formatted_text);
        END;

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            remind_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            fired INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_reminders_fired
            ON reminders(fired, remind_at);
    """)
    await db.commit()
    logger.info("Database initialized at %s", db_path)


# ---- Transcriptions CRUD ----

async def save_transcription(
    user_id: int,
    raw_text: str,
    formatted_text: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    priority: str | None = None,
    summary: str | None = None,
    action_items: list[str] | None = None,
    sentiment: str | None = None,
    duration: float | None = None,
    source: str = "voice",
    mode: str = "dictation",
) -> int:
    db = await get_db()
    tags_json = json.dumps(tags, ensure_ascii=False) if tags else None
    items_json = json.dumps(action_items, ensure_ascii=False) if action_items else None

    cursor = await db.execute(
        """INSERT INTO transcriptions
           (user_id, raw_text, formatted_text, category, tags, priority,
            summary, action_items, sentiment, duration, source, mode)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, raw_text, formatted_text, category, tags_json, priority,
         summary, items_json, sentiment, duration, source, mode),
    )
    await db.commit()
    logger.info("Saved transcription id=%s for user=%s", cursor.lastrowid, user_id)
    return cursor.lastrowid


async def search_transcriptions(
    user_id: int, query: str, limit: int = 10
) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT t.* FROM transcriptions t
           JOIN transcriptions_fts fts ON t.id = fts.rowid
           WHERE transcriptions_fts MATCH ? AND t.user_id = ?
           ORDER BY t.created_at DESC
           LIMIT ?""",
        (query, user_id, limit),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_history(
    user_id: int, limit: int = 10, offset: int = 0
) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM transcriptions
           WHERE user_id = ?
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (user_id, limit, offset),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_transcriptions_since(
    user_id: int, since: str
) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM transcriptions
           WHERE user_id = ? AND created_at >= ?
           ORDER BY created_at ASC""",
        (user_id, since),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ---- Reminders CRUD ----

async def save_reminder(user_id: int, text: str, remind_at: str) -> int:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO reminders (user_id, text, remind_at)
           VALUES (?, ?, ?)""",
        (user_id, text, remind_at),
    )
    await db.commit()
    logger.info("Saved reminder id=%s for user=%s at %s", cursor.lastrowid, user_id, remind_at)
    return cursor.lastrowid


async def get_pending_reminders() -> list[dict]:
    db = await get_db()
    now = datetime.now().isoformat()
    cursor = await db.execute(
        """SELECT * FROM reminders
           WHERE fired = 0 AND remind_at <= ?
           ORDER BY remind_at ASC""",
        (now,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def mark_reminder_fired(reminder_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE reminders SET fired = 1 WHERE id = ?",
        (reminder_id,),
    )
    await db.commit()


async def get_user_reminders(user_id: int) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM reminders
           WHERE user_id = ? AND fired = 0
           ORDER BY remind_at ASC""",
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ---- Diary / mood helpers ----

async def get_diary_entries(
    user_id: int, since: str | None = None, limit: int = 50
) -> list[dict]:
    db = await get_db()
    if since:
        cursor = await db.execute(
            """SELECT * FROM transcriptions
               WHERE user_id = ? AND category = 'journal' AND created_at >= ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, since, limit),
        )
    else:
        cursor = await db.execute(
            """SELECT * FROM transcriptions
               WHERE user_id = ? AND category = 'journal'
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_mood_stats(user_id: int, days: int = 7) -> dict:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    db = await get_db()
    cursor = await db.execute(
        """SELECT sentiment, COUNT(*) as cnt FROM transcriptions
           WHERE user_id = ? AND created_at >= ? AND sentiment IS NOT NULL
           GROUP BY sentiment""",
        (user_id, since),
    )
    rows = await cursor.fetchall()
    stats = {"positive": 0, "neutral": 0, "negative": 0}
    for row in rows:
        key = row["sentiment"]
        if key in stats:
            stats[key] = row["cnt"]
    return stats
