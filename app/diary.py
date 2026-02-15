import asyncio
import json
import logging

from . import db
from .groq_client import llm_call
from .prompts import WEEKLY_REVIEW

logger = logging.getLogger(__name__)


async def save_diary_entry(
    user_id: int,
    raw_text: str,
    formatted_text: str,
    duration: float | None = None,
) -> int:
    return await db.save_transcription(
        user_id=user_id,
        raw_text=raw_text,
        formatted_text=formatted_text,
        category="journal",
        source="voice",
        mode="dictation",
        duration=duration,
    )


async def generate_weekly_review(user_id: int) -> str | None:
    from datetime import datetime, timedelta

    since = (datetime.now() - timedelta(days=7)).isoformat()
    entries = await db.get_diary_entries(user_id, since=since)
    if not entries:
        return None

    text = "\n\n---\n\n".join(
        f"[{e['created_at']}]\n{e.get('formatted_text') or e['raw_text']}"
        for e in entries
    )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, llm_call, WEEKLY_REVIEW, text)


async def export_diary(user_id: int, fmt: str = "markdown") -> str:
    entries = await db.get_diary_entries(user_id, limit=500)

    if not entries:
        return "Нет записей в дневнике."

    if fmt == "json":
        return json.dumps(entries, ensure_ascii=False, indent=2, default=str)

    lines = ["# Голосовой дневник\n"]
    for entry in entries:
        dt = entry.get("created_at", "?")
        text = entry.get("formatted_text") or entry.get("raw_text", "")
        sentiment = entry.get("sentiment")
        mood_icon = ""
        if sentiment == "positive":
            mood_icon = " 😊"
        elif sentiment == "negative":
            mood_icon = " 😔"

        lines.append(f"## {dt}{mood_icon}\n")
        lines.append(f"{text}\n")
        lines.append("---\n")

    return "\n".join(lines)


async def get_mood_summary(user_id: int, days: int = 7) -> str:
    stats = await db.get_mood_stats(user_id, days)
    total = sum(stats.values())

    if total == 0:
        return f"За последние {days} дней нет записей с анализом настроения."

    return (
        f"За последние {days} дней ({total} записей):\n"
        f"😊 Позитивных: {stats['positive']}\n"
        f"😐 Нейтральных: {stats['neutral']}\n"
        f"😔 Негативных: {stats['negative']}"
    )
