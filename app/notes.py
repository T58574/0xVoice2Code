import logging
import os
from datetime import datetime

from . import db

logger = logging.getLogger(__name__)

NOTES_DIR = "notes"

RUSSIAN_MONTHS = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def save_note_to_file(formatted_text: str) -> str:
    now = datetime.now()
    dir_path = os.path.join(
        NOTES_DIR,
        now.strftime("%Y"),
        now.strftime("%m"),
        now.strftime("%d"),
    )
    os.makedirs(dir_path, exist_ok=True)

    filename = now.strftime("%H-%M-%S") + ".md"
    file_path = os.path.join(dir_path, filename)

    month_name = RUSSIAN_MONTHS[now.month]
    date_header = f"{now.day} {month_name} {now.year}, {now.strftime('%H:%M')}"

    content = f"> {date_header}\n\n{formatted_text}\n"

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info("Saved note to %s", file_path)
    return file_path


async def save_note_to_db(
    user_id: int,
    raw_text: str,
    formatted_text: str,
    duration: float | None = None,
) -> int:
    return await db.save_transcription(
        user_id=user_id,
        raw_text=raw_text,
        formatted_text=formatted_text,
        category="note",
        source="voice",
        mode="note",
        duration=duration,
    )


def list_recent_notes(limit: int = 10) -> list[dict]:
    if not os.path.isdir(NOTES_DIR):
        return []

    all_notes: list[dict] = []

    for root, _dirs, files in os.walk(NOTES_DIR):
        for fname in files:
            if not fname.endswith(".md"):
                continue

            file_path = os.path.join(root, fname)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError as e:
                logger.warning("Cannot read note %s: %s", file_path, e)
                continue

            lines = content.split("\n")
            date_str = ""
            text_start = 0
            for i, line in enumerate(lines):
                if line.startswith("> "):
                    date_str = line[2:].strip()
                    text_start = i + 1
                    break

            while text_start < len(lines) and not lines[text_start].strip():
                text_start += 1

            preview_text = "\n".join(lines[text_start:]).strip()
            preview = preview_text[:100]

            all_notes.append({
                "path": file_path,
                "date": date_str,
                "preview": preview,
            })

    all_notes.sort(key=lambda n: n["path"], reverse=True)
    return all_notes[:limit]


def format_notes_list(notes_list: list[dict]) -> str:
    if not notes_list:
        return "\U0001f4dd Заметок пока нет."

    lines: list[str] = []
    for note in notes_list:
        preview = note["preview"]
        if len(note["preview"]) >= 100:
            preview += "..."
        lines.append(f"\U0001f4dd {note['date']}\n{preview}\n")

    return "\n".join(lines)
