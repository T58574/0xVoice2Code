"""Groq API wrapper: client, transcription, formatting, categorization, rate limits."""

import json
import logging
import re

from groq import Groq

from .config import GROQ_API_KEY
from .prompts import MODE_PROMPTS, TRANSCRIPTION_CLEANUP, CATEGORIZE

log = logging.getLogger(__name__)

_client: Groq | None = None
_last_headers: dict[str, dict[str, str]] = {"whisper": {}, "llm": {}}


def get_client() -> Groq | None:
    global _client
    if _client is None and GROQ_API_KEY:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def _save_headers(headers, key: str) -> None:
    info = {}
    for name, val in headers.items():
        lower = name.lower()
        if "ratelimit" in lower:
            info[lower] = val
    if info:
        _last_headers[key] = info


def transcribe(audio_bytes: bytes) -> str | None:
    client = get_client()
    if not client:
        return None
    try:
        response = client.audio.transcriptions.with_raw_response.create(
            file=("voice.ogg", audio_bytes),
            model="whisper-large-v3",
            language="ru",
        )
        _save_headers(response.headers, "whisper")
        return response.parse().text
    except Exception as e:
        log.error("Groq transcription error: %s", e)
        return None


def format_text(raw_text: str, mode: str = "dictation") -> str | None:
    client = get_client()
    if not client:
        return None
    system_prompt = MODE_PROMPTS.get(mode, TRANSCRIPTION_CLEANUP)
    try:
        response = client.chat.completions.with_raw_response.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"<transcript>{raw_text}</transcript>"},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        _save_headers(response.headers, "llm")
        return response.parse().choices[0].message.content
    except Exception as e:
        log.error("Groq API error: %s", e)
        return None


def categorize(text: str) -> dict | None:
    client = get_client()
    if not client:
        return None
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": CATEGORIZE},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(content)
    except (json.JSONDecodeError, Exception) as e:
        log.error("Categorization error: %s", e)
        return None


def llm_call(system_prompt: str, user_text: str) -> str | None:
    """Generic synchronous Groq LLM call (used by diary, scheduler, etc.)."""
    client = get_client()
    if not client:
        return None
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        return response.choices[0].message.content
    except Exception as e:
        log.error("LLM call error: %s", e)
        return None


# ---- Rate limit formatting ----

def _fmt_sec(val: str) -> str:
    """'7195' -> '1ч 59м'"""
    try:
        total = int(float(val))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}ч {m}м"
        if m > 0:
            return f"{m}м {s}с"
        return f"{s}с"
    except (ValueError, TypeError):
        return val


def _fmt_reset(val: str) -> str:
    """'2m52.8s' -> '2м 52с', '43.2s' -> '43с'"""
    if not val:
        return "?"
    result = val
    result = re.sub(r"(\d+)\.\d+s", r"\1с", result)
    result = result.replace("m", "м ").replace("s", "с").replace("h", "ч ")
    return result.strip()


def format_limits() -> str:
    lines = []

    w = _last_headers.get("whisper", {})
    if w:
        rem = w.get("x-ratelimit-remaining-audio-seconds", "?")
        lim = w.get("x-ratelimit-limit-audio-seconds", "?")
        reset = _fmt_reset(w.get("x-ratelimit-reset-audio-seconds", ""))
        req_rem = w.get("x-ratelimit-remaining-requests", "?")
        req_lim = w.get("x-ratelimit-limit-requests", "?")
        req_reset = _fmt_reset(w.get("x-ratelimit-reset-requests", ""))

        lines.append("🎙 Whisper:")
        lines.append(f"  Аудио: {_fmt_sec(rem)} / {_fmt_sec(lim)} (сброс: {reset})")
        lines.append(f"  Запросы: {req_rem}/{req_lim} (сброс: {req_reset})")

    ll = _last_headers.get("llm", {})
    if ll:
        req_rem = ll.get("x-ratelimit-remaining-requests", "?")
        req_lim = ll.get("x-ratelimit-limit-requests", "?")
        req_reset = _fmt_reset(ll.get("x-ratelimit-reset-requests", ""))
        tok_rem = ll.get("x-ratelimit-remaining-tokens", "?")
        tok_lim = ll.get("x-ratelimit-limit-tokens", "?")
        tok_reset = _fmt_reset(ll.get("x-ratelimit-reset-tokens", ""))

        lines.append("\n🤖 LLM:")
        lines.append(f"  Запросы: {req_rem}/{req_lim} (сброс: {req_reset})")
        lines.append(f"  Токены: {tok_rem}/{tok_lim} (сброс: {tok_reset})")

    if not lines:
        return "Лимиты пока неизвестны — отправь голосовое."

    return "\n".join(lines)


def format_limits_short() -> str:
    w = _last_headers.get("whisper", {})
    rem = w.get("x-ratelimit-remaining-audio-seconds")
    lim = w.get("x-ratelimit-limit-audio-seconds")
    req_rem = w.get("x-ratelimit-remaining-requests")
    req_lim = w.get("x-ratelimit-limit-requests")
    parts = []
    if rem and lim:
        parts.append(f"🎙 {_fmt_sec(rem)}/{_fmt_sec(lim)}")
    if req_rem and req_lim:
        parts.append(f"📨 {req_rem}/{req_lim}")
    return " · ".join(parts)
