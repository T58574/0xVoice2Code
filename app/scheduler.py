import asyncio
import json
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import db
from .groq_client import llm_call
from .prompts import DAILY_DIGEST, WEEKLY_DIGEST, REMINDER_PARSE

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot = None
_user_id: int = 0


# ---- Digests ----

async def generate_daily_digest(user_id: int) -> str | None:
    since = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    entries = await db.get_transcriptions_since(user_id, since)
    if not entries:
        return None

    text = "\n\n---\n\n".join(
        f"[{e['created_at']}] ({e.get('category', 'N/A')})\n{e.get('formatted_text') or e['raw_text']}"
        for e in entries
    )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, llm_call, DAILY_DIGEST, text)


async def generate_weekly_digest(user_id: int) -> str | None:
    since = (datetime.now() - timedelta(days=7)).isoformat()
    entries = await db.get_transcriptions_since(user_id, since)
    if not entries:
        return None

    text = "\n\n---\n\n".join(
        f"[{e['created_at']}] ({e.get('category', 'N/A')})\n{e.get('formatted_text') or e['raw_text']}"
        for e in entries
    )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, llm_call, WEEKLY_DIGEST, text)


# ---- Reminders ----

async def parse_reminder_from_text(text: str) -> dict | None:
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, llm_call, REMINDER_PARSE, text)
    if not result:
        return None
    try:
        content = result.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Reminder parse error: %s (raw: %s)", e, result)
        return None


async def create_reminder(user_id: int, text: str, delay_seconds: int) -> int:
    remind_at = (datetime.now() + timedelta(seconds=delay_seconds)).isoformat()
    return await db.save_reminder(user_id, text, remind_at)


async def check_reminders() -> None:
    try:
        pending = await db.get_pending_reminders()
        for reminder in pending:
            user_id = reminder["user_id"]
            text = reminder["text"]
            try:
                if _bot:
                    await _bot.send_message(user_id, f"🔔 Напоминание:\n{text}")
                await db.mark_reminder_fired(reminder["id"])
                logger.info("Fired reminder id=%s for user=%s", reminder["id"], user_id)
            except Exception as e:
                logger.error("Failed to send reminder id=%s: %s", reminder["id"], e)
    except Exception as e:
        logger.error("check_reminders error: %s", e)


# ---- Digest delivery ----

async def _send_daily_digest() -> None:
    if not _bot or not _user_id:
        return
    digest = await generate_daily_digest(_user_id)
    if digest:
        try:
            await _bot.send_message(_user_id, f"📊 Дневной дайджест:\n\n{digest}")
        except Exception as e:
            logger.error("Failed to send daily digest: %s", e)


async def _send_weekly_digest() -> None:
    if not _bot or not _user_id:
        return
    digest = await generate_weekly_digest(_user_id)
    if digest:
        try:
            await _bot.send_message(_user_id, f"📅 Недельный дайджест:\n\n{digest}")
        except Exception as e:
            logger.error("Failed to send weekly digest: %s", e)


# ---- Scheduler lifecycle ----

async def start_scheduler(bot, user_id: int) -> None:
    global _scheduler, _bot, _user_id
    _bot = bot
    _user_id = user_id

    _scheduler = AsyncIOScheduler()

    _scheduler.add_job(
        _send_daily_digest, "cron",
        hour=21, minute=0,
        id="daily_digest", replace_existing=True,
    )
    _scheduler.add_job(
        _send_weekly_digest, "cron",
        day_of_week="sun", hour=20, minute=0,
        id="weekly_digest", replace_existing=True,
    )
    _scheduler.add_job(
        check_reminders, "interval",
        seconds=30,
        id="reminder_check", replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started (daily 21:00, weekly Sun 20:00, reminders every 30s)")


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
