"""Telegram message and command handlers."""

import asyncio
import io
import logging
import re
import subprocess

from aiogram import Bot, Router, F
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import CommandStart, Command

from .config import TELEGRAM_USER_ID
from . import groq_client
from . import commands
from . import db
from . import diary
from . import notes
from . import scheduler
from .vision import analyze_photo, analyze_photo_with_voice

log = logging.getLogger(__name__)

router = Router()

# Per-user processing mode
user_modes: dict[int, str] = {}

# Pending dangerous command confirmations
pending_confirmations: dict[int, dict] = {}

# Wake word detection
WAKE_WORD_PATTERN = re.compile(
    r"^\s*(гекс|гексик|hex|гекси|гексу|heks)\b[,.\s!]*",
    re.IGNORECASE,
)


def is_authorized(user_id: int) -> bool:
    return TELEGRAM_USER_ID == 0 or user_id == TELEGRAM_USER_ID


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run("clip", input=text.encode("utf-16-le"), check=True)
        return True
    except Exception as e:
        log.error("Clipboard error: %s", e)
        return False


def extract_command_text(transcribed: str) -> str | None:
    match = WAKE_WORD_PATTERN.match(transcribed)
    if match:
        return transcribed[match.end():].strip()
    return None


# ---- Voice command execution ----

async def handle_command_voice(
    message: Message,
    bot: Bot,
    status_msg: Message,
    command_text: str,
    loop: asyncio.AbstractEventLoop,
) -> None:
    await status_msg.edit_text(f"🎯 Команда: {command_text}\n⏳ Разбираю...")

    intent_data = await loop.run_in_executor(None, commands.parse_intent, command_text)

    if not intent_data or intent_data.get("intent") == "unknown":
        await status_msg.edit_text(
            f"🎯 Команда: {command_text}\n❌ Не удалось распознать."
        )
        return

    intent = intent_data["intent"]
    params = intent_data.get("params", {})

    if intent in commands.DANGEROUS_INTENTS:
        label = commands.COMMAND_REGISTRY.get(intent, {}).get("label", intent)
        pending_confirmations[message.from_user.id] = intent_data
        await status_msg.edit_text(
            f"⚠️ {label}\n"
            f"Параметры: {params}\n\n"
            f"Отправь 'да' для подтверждения или 'нет' для отмены."
        )
        return

    if intent == "screenshot":
        await status_msg.edit_text("📸 Делаю скриншот...")
        png_bytes = await loop.run_in_executor(None, commands.take_screenshot_bytes)
        if png_bytes:
            photo = BufferedInputFile(png_bytes, filename="screenshot.png")
            await message.answer_document(photo)
            await status_msg.edit_text("📸 Скриншот отправлен.")
        else:
            await status_msg.edit_text("❌ Не удалось сделать скриншот.")
        return

    result = await loop.run_in_executor(None, commands.execute_command, intent, params)
    await status_msg.edit_text(f"🎯 {command_text}\n{result}")


# ---- Telegram command handlers ----

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    await message.answer(
        "Привет! Отправь мне голосовое сообщение, и я:\n"
        "1. Транскрибирую его в текст (Whisper)\n"
        "2. Отформатирую и категоризирую (Groq)\n\n"
        "🎯 Голосовое управление ПК:\n"
        "Начни фразу с «Гекс» — и я выполню команду.\n\n"
        "📸 Фото: отправь фото — получи анализ.\n\n"
        "📋 Команды:\n"
        "/mode_meeting — режим митинга\n"
        "/mode_idea — режим идей\n"
        "/mode_dictation — режим диктовки (по умолчанию)\n"
        "/note — режим заметки (сны, мысли, идеи)\n"
        "/notes — список последних заметок\n"
        "/search [запрос] — поиск по записям\n"
        "/history — последние записи\n"
        "/diary — сохранить как дневник\n"
        "/week — обзор недели\n"
        "/mood — настроение за неделю\n"
        "/export — экспорт дневника\n"
        "/reminders — активные напоминания\n"
        "/limits — лимиты Groq API\n"
        "/commands — голосовые команды"
    )


@router.message(Command("limits"))
async def cmd_limits(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    await message.answer(groq_client.format_limits())


@router.message(Command("commands"))
async def cmd_commands(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    lines = ["🎯 Доступные голосовые команды:\n"]
    for intent, info in commands.COMMAND_REGISTRY.items():
        danger = " ⚠️" if intent in commands.DANGEROUS_INTENTS else ""
        lines.append(f"• {info['label']}{danger}")
    lines.append("\nНачни фразу с «Гекс» + команда.")
    await message.answer("\n".join(lines))


# ---- Mode commands ----

@router.message(Command("mode_meeting"))
async def cmd_mode_meeting(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    user_modes[message.from_user.id] = "meeting"
    await message.answer("📝 Режим: Митинг. Следующие голосовые будут структурированы как заметки встречи.")


@router.message(Command("mode_idea"))
async def cmd_mode_idea(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    user_modes[message.from_user.id] = "idea"
    await message.answer("💡 Режим: Идея. Следующие голосовые будут оформлены как идеи/брейнсторм.")


@router.message(Command("mode_dictation"))
async def cmd_mode_dictation(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    user_modes[message.from_user.id] = "dictation"
    await message.answer("🎤 Режим: Диктовка (по умолчанию). Минимальная очистка текста.")


# ---- Search & History ----

@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    query = (message.text or "").replace("/search", "", 1).strip()
    if not query:
        await message.answer("Укажи запрос: /search ключевое слово")
        return

    results = await db.search_transcriptions(message.from_user.id, query, limit=5)
    if not results:
        await message.answer(f"🔍 По запросу «{query}» ничего не найдено.")
        return

    lines = [f"🔍 Результаты по «{query}»:\n"]
    for r in results:
        dt = r.get("created_at", "?")[:16]
        text = (r.get("formatted_text") or r.get("raw_text", ""))[:150]
        cat = r.get("category") or "—"
        lines.append(f"📄 [{dt}] ({cat})\n{text}...\n")

    await message.answer("\n".join(lines))


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    entries = await db.get_history(message.from_user.id, limit=5)
    if not entries:
        await message.answer("📚 История пуста.")
        return

    lines = ["📚 Последние записи:\n"]
    for e in entries:
        dt = e.get("created_at", "?")[:16]
        text = (e.get("formatted_text") or e.get("raw_text", ""))[:150]
        cat = e.get("category") or "—"
        lines.append(f"📄 [{dt}] ({cat})\n{text}...\n")

    await message.answer("\n".join(lines))


# ---- Diary commands ----

@router.message(Command("diary"))
async def cmd_diary(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    await message.answer(
        "📔 Режим дневника активен.\n"
        "Отправь голосовое — оно будет сохранено как запись дневника.\n"
        "Используй /week для обзора недели, /mood для статистики настроения."
    )
    user_modes[message.from_user.id] = "diary"


@router.message(Command("week"))
async def cmd_week(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    status = await message.answer("⏳ Готовлю обзор недели...")
    review = await diary.generate_weekly_review(message.from_user.id)
    if review:
        await status.edit_text(f"📅 Обзор недели:\n\n{review}")
    else:
        await status.edit_text("📅 За эту неделю нет записей в дневнике.")


@router.message(Command("mood"))
async def cmd_mood(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    summary = await diary.get_mood_summary(message.from_user.id)
    await message.answer(summary)


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    args = (message.text or "").replace("/export", "", 1).strip().lower()
    fmt = "json" if args == "json" else "markdown"

    content = await diary.export_diary(message.from_user.id, fmt=fmt)
    ext = "json" if fmt == "json" else "md"
    file_bytes = content.encode("utf-8")
    doc = BufferedInputFile(file_bytes, filename=f"diary.{ext}")
    await message.answer_document(doc, caption=f"📔 Экспорт дневника ({fmt})")


# ---- Notes ----

@router.message(Command("note"))
async def cmd_note(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    user_modes[message.from_user.id] = "note"
    await message.answer(
        "📝 Режим заметки активен.\n"
        "Отправь голосовое — оно будет сохранено как заметка.\n"
        "Заметки хранятся в папке notes/ по датам."
    )


@router.message(Command("notes"))
async def cmd_notes(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    loop = asyncio.get_running_loop()
    recent = await loop.run_in_executor(None, notes.list_recent_notes, 10)
    text = notes.format_notes_list(recent)
    await message.answer(text)


# ---- Reminders ----

@router.message(Command("reminders"))
async def cmd_reminders(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    reminders = await db.get_user_reminders(message.from_user.id)
    if not reminders:
        await message.answer("🔔 Нет активных напоминаний.")
        return

    lines = ["🔔 Активные напоминания:\n"]
    for r in reminders:
        dt = r.get("remind_at", "?")[:16]
        lines.append(f"• [{dt}] {r['text']}")

    await message.answer("\n".join(lines))


# ---- Photo handler ----

@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return

    status_msg = await message.answer("📸 Анализирую фото...")

    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        if not file.file_path:
            await status_msg.edit_text("Не удалось получить фото.")
            return

        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        image_bytes = buf.getvalue()

        caption = message.caption or None
        loop = asyncio.get_running_loop()

        if caption:
            result = await loop.run_in_executor(
                None, analyze_photo_with_voice, image_bytes, caption
            )
        else:
            result = await loop.run_in_executor(
                None, analyze_photo, image_bytes, None
            )

        if result:
            await status_msg.edit_text(f"📸 Анализ:\n\n{result}")
        else:
            await status_msg.edit_text("❌ Не удалось проанализировать фото.")

    except Exception as e:
        log.exception("Error processing photo")
        await status_msg.edit_text(f"Ошибка: {e}")


# ---- Voice handler ----

@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return
    if not message.voice:
        return

    status_msg = await message.answer("⏳ Транскрибирую...")

    try:
        file = await bot.get_file(message.voice.file_id)
        if not file.file_path:
            await status_msg.edit_text("Не удалось получить файл.")
            return

        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        audio_bytes = buf.getvalue()

        loop = asyncio.get_running_loop()
        raw_text = await loop.run_in_executor(None, groq_client.transcribe, audio_bytes)

        if not raw_text or not raw_text.strip():
            await status_msg.edit_text("Не удалось распознать речь.")
            return

        command_text = extract_command_text(raw_text)

        if command_text:
            # Check for reminder intent
            reminder_keywords = ("напомни", "напоминание", "remind")
            if any(kw in command_text.lower() for kw in reminder_keywords):
                await status_msg.edit_text("🔔 Разбираю напоминание...")
                parsed = await scheduler.parse_reminder_from_text(command_text)
                if parsed and "delay_seconds" in parsed and "text" in parsed:
                    rid = await scheduler.create_reminder(
                        message.from_user.id, parsed["text"], parsed["delay_seconds"]
                    )
                    minutes = parsed["delay_seconds"] // 60
                    await status_msg.edit_text(
                        f"🔔 Напоминание создано (id={rid}):\n"
                        f"«{parsed['text']}» через {minutes} мин."
                    )
                else:
                    await status_msg.edit_text("❌ Не удалось разобрать напоминание.")
                return

            await handle_command_voice(message, bot, status_msg, command_text, loop)
        else:
            user_id = message.from_user.id
            mode = user_modes.get(user_id, "dictation")
            is_diary = mode == "diary"
            is_note = mode == "note"
            if is_diary:
                mode = "dictation"

            await status_msg.edit_text("⏳ Форматирую...")
            formatted = await loop.run_in_executor(
                None, groq_client.format_text, raw_text, mode
            )

            clean_text = formatted or raw_text

            # Auto-categorize
            await status_msg.edit_text("⏳ Категоризирую...")
            meta = await loop.run_in_executor(None, groq_client.categorize, clean_text)

            duration = message.voice.duration if message.voice else None

            if is_note:
                file_path = await loop.run_in_executor(
                    None, notes.save_note_to_file, clean_text
                )
                await notes.save_note_to_db(
                    user_id, raw_text, clean_text, duration=duration
                )
                cat_label = f"📝 заметка ({file_path})"
            elif is_diary:
                await diary.save_diary_entry(
                    user_id, raw_text, clean_text, duration=duration
                )
                cat_label = "📔 дневник"
            else:
                category = meta.get("category") if meta else None
                tags = meta.get("tags") if meta else None
                priority = meta.get("priority") if meta else None
                summary = meta.get("summary") if meta else None
                action_items = meta.get("action_items") if meta else None
                sentiment = meta.get("sentiment") if meta else None

                await db.save_transcription(
                    user_id=user_id,
                    raw_text=raw_text,
                    formatted_text=clean_text,
                    category=category,
                    tags=tags,
                    priority=priority,
                    summary=summary,
                    action_items=action_items,
                    sentiment=sentiment,
                    duration=duration,
                    source="voice",
                    mode=mode,
                )
                cat_label = f"📂 {category}" if category else ""

            copied = copy_to_clipboard(clean_text)
            clip_icon = "📋" if copied else "⚠️"
            limits = groq_client.format_limits_short()

            footer_parts = [clip_icon]
            if cat_label:
                footer_parts.append(cat_label)
            if limits:
                footer_parts.append(limits)
            footer = " | ".join(footer_parts)

            result = f"{clean_text}\n\n{footer}"
            await status_msg.edit_text(result)

    except Exception as e:
        log.exception("Error processing voice message")
        await status_msg.edit_text(f"Ошибка: {e}")


@router.message(F.text)
async def handle_text(message: Message) -> None:
    if not message.from_user or not is_authorized(message.from_user.id):
        return

    user_id = message.from_user.id
    text = message.text.strip().lower()

    if user_id in pending_confirmations:
        intent_data = pending_confirmations.pop(user_id)
        if text in ("да", "yes", "ок", "ok", "подтверждаю", "давай"):
            loop = asyncio.get_running_loop()
            intent = intent_data["intent"]
            params = intent_data.get("params", {})
            result = await loop.run_in_executor(
                None, commands.execute_command, intent, params
            )
            await message.answer(f"✅ {result}")
        else:
            await message.answer("❌ Команда отменена.")
