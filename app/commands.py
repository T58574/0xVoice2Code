"""PC voice commands: intent parsing, command handlers, macros."""

import json
import logging
import os
import subprocess
import time

import pyautogui
import mss
import mss.tools

from .prompts import INTENT_PARSE
from .groq_client import get_client

log = logging.getLogger(__name__)

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05


def parse_intent(command_text: str) -> dict | None:
    client = get_client()
    if not client:
        return None
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": INTENT_PARSE},
                {"role": "user", "content": command_text},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        content = response.choices[0].message.content
        if not content:
            return None
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(content)
    except (json.JSONDecodeError, Exception) as e:
        log.error("Intent parsing error: %s", e)
        return None


DANGEROUS_INTENTS = {"shutdown", "restart", "hibernate"}


# ---- Command handlers ----

def cmd_shutdown(params: dict) -> str:
    delay = params.get("delay_seconds", 60)
    subprocess.run(
        ["shutdown", "/s", "/t", str(delay)],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return f"⏻ Выключение через {delay} сек."


def cmd_restart(params: dict) -> str:
    subprocess.run(
        ["shutdown", "/r", "/t", "5"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "🔄 Перезагрузка через 5 сек."


def cmd_cancel_shutdown(params: dict) -> str:
    subprocess.run(
        ["shutdown", "/a"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "🚫 Выключение/перезагрузка отменены."


def cmd_sleep(params: dict) -> str:
    subprocess.run(
        ["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "😴 ПК уходит в сон."


def cmd_lock(params: dict) -> str:
    subprocess.run(
        ["rundll32.exe", "user32.dll,LockWorkStation"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "🔒 Экран заблокирован."


def cmd_hibernate(params: dict) -> str:
    subprocess.run(
        ["shutdown", "/h"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "❄️ Гибернация."


def cmd_open_app(params: dict) -> str:
    name = params.get("name", "")
    if not name:
        return "❌ Не указано имя приложения."
    try:
        subprocess.Popen(name, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return f"🚀 Открываю {name}."
    except Exception as e:
        return f"❌ Не удалось открыть {name}: {e}"


def cmd_close_app(params: dict) -> str:
    name = params.get("name", "")
    if not name:
        return "❌ Не указано имя приложения."
    proc_name = name if name.endswith(".exe") else f"{name}.exe"
    result = subprocess.run(
        ["taskkill", "/IM", proc_name, "/F"],
        capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode == 0:
        return f"💀 Процесс {proc_name} завершён."
    return f"❌ Не удалось завершить {proc_name}: {result.stderr.strip()}"


def cmd_volume_up(params: dict) -> str:
    steps = max(1, params.get("percent", 10) // 2)
    for _ in range(steps):
        pyautogui.press("volumeup")
    return f"🔊 Громкость +{steps * 2}%."


def cmd_volume_down(params: dict) -> str:
    steps = max(1, params.get("percent", 10) // 2)
    for _ in range(steps):
        pyautogui.press("volumedown")
    return f"🔉 Громкость -{steps * 2}%."


def cmd_volume_mute(params: dict) -> str:
    pyautogui.press("volumemute")
    return "🔇 Звук переключён (mute/unmute)."


def cmd_media_play_pause(params: dict) -> str:
    pyautogui.press("playpause")
    return "⏯ Play/Pause."


def cmd_media_next(params: dict) -> str:
    pyautogui.press("nexttrack")
    return "⏭ Следующий трек."


def cmd_media_prev(params: dict) -> str:
    pyautogui.press("prevtrack")
    return "⏮ Предыдущий трек."


def cmd_screenshot(params: dict) -> str:
    return "SCREENSHOT_REQUESTED"


def take_screenshot_bytes() -> bytes | None:
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            img = sct.grab(monitor)
            return mss.tools.to_png(img.rgb, img.size)
    except Exception as e:
        log.error("Screenshot error: %s", e)
        return None


def cmd_type_text(params: dict) -> str:
    text = params.get("text", "")
    if not text:
        return "❌ Не указан текст для ввода."
    try:
        subprocess.run("clip", input=text.encode("utf-16-le"), check=True)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        return f"⌨️ Текст введён: {text[:50]}{'...' if len(text) > 50 else ''}"
    except Exception as e:
        return f"❌ Ошибка ввода текста: {e}"


def cmd_open_url(params: dict) -> str:
    url = params.get("url", "")
    if not url:
        return "❌ Не указан URL."
    try:
        os.startfile(url)
        return f"🌐 Открываю {url}"
    except Exception as e:
        return f"❌ Не удалось открыть URL: {e}"


def cmd_hotkey(params: dict) -> str:
    keys = params.get("keys", [])
    if not keys:
        return "❌ Не указаны клавиши."
    try:
        pyautogui.hotkey(*keys)
        return f"⌨️ Нажато: {' + '.join(keys)}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


# ---- Macros ----

MACRO_REGISTRY: dict[str, dict] = {
    "start_work": {
        "label": "Начать рабочий день",
        "steps": [
            {"intent": "open_app", "params": {"name": "telegram"}},
            {"intent": "open_url", "params": {"url": "https://mail.google.com"}},
            {"intent": "open_app", "params": {"name": "code"}},
        ],
    },
    "end_work": {
        "label": "Закончить рабочий день",
        "steps": [
            {"intent": "close_app", "params": {"name": "code"}},
            {"intent": "lock", "params": {}},
        ],
    },
    "music_mode": {
        "label": "Режим музыки",
        "steps": [
            {"intent": "open_url", "params": {"url": "https://music.youtube.com"}},
            {"intent": "volume_up", "params": {"percent": 50}},
        ],
    },
    "focus_mode": {
        "label": "Режим фокуса",
        "steps": [
            {"intent": "volume_mute", "params": {}},
            {"intent": "close_app", "params": {"name": "telegram"}},
        ],
    },
    "presentation": {
        "label": "Режим презентации",
        "steps": [
            {"intent": "volume_up", "params": {"percent": 70}},
            {"intent": "hotkey", "params": {"keys": ["win", "p"]}},
        ],
    },
}


def execute_command(intent: str, params: dict) -> str:
    entry = COMMAND_REGISTRY.get(intent)
    if not entry:
        return f"❌ Неизвестная команда: {intent}"
    try:
        return entry["handler"](params)
    except Exception as e:
        log.error("Command execution error [%s]: %s", intent, e)
        return f"❌ Ошибка выполнения {intent}: {e}"


def execute_macro(macro_name: str) -> str:
    macro = MACRO_REGISTRY.get(macro_name)
    if not macro:
        return f"❌ Неизвестный макрос: {macro_name}"

    label = macro["label"]
    results = []
    for step in macro["steps"]:
        intent = step["intent"]
        params = step.get("params", {})
        try:
            result = execute_command(intent, params)
            results.append(f"✅ {result}")
        except Exception as e:
            results.append(f"❌ {intent}: {e}")

    steps_text = "\n".join(f"  • {r}" for r in results)
    return f"🔗 Макрос «{label}»:\n{steps_text}"


def cmd_run_macro(params: dict) -> str:
    macro_name = params.get("macro", "")
    if not macro_name:
        return "❌ Не указан макрос."
    return execute_macro(macro_name)


def cmd_list_macros(params: dict) -> str:
    lines = ["📋 Доступные макросы:\n"]
    for name, info in MACRO_REGISTRY.items():
        steps_count = len(info["steps"])
        lines.append(f"• {info['label']} ({name}) — {steps_count} шагов")
    return "\n".join(lines)


COMMAND_REGISTRY: dict[str, dict] = {
    "shutdown":         {"handler": cmd_shutdown,         "label": "Выключение ПК"},
    "restart":          {"handler": cmd_restart,          "label": "Перезагрузка"},
    "cancel_shutdown":  {"handler": cmd_cancel_shutdown,  "label": "Отмена выключения"},
    "sleep":            {"handler": cmd_sleep,            "label": "Сон"},
    "lock":             {"handler": cmd_lock,             "label": "Блокировка"},
    "hibernate":        {"handler": cmd_hibernate,        "label": "Гибернация"},
    "open_app":         {"handler": cmd_open_app,         "label": "Открытие приложения"},
    "close_app":        {"handler": cmd_close_app,        "label": "Закрытие приложения"},
    "volume_up":        {"handler": cmd_volume_up,        "label": "Громкость +"},
    "volume_down":      {"handler": cmd_volume_down,      "label": "Громкость -"},
    "volume_mute":      {"handler": cmd_volume_mute,      "label": "Mute"},
    "media_play_pause": {"handler": cmd_media_play_pause, "label": "Play/Pause"},
    "media_next":       {"handler": cmd_media_next,       "label": "Следующий трек"},
    "media_prev":       {"handler": cmd_media_prev,       "label": "Предыдущий трек"},
    "screenshot":       {"handler": cmd_screenshot,       "label": "Скриншот"},
    "type_text":        {"handler": cmd_type_text,        "label": "Ввод текста"},
    "open_url":         {"handler": cmd_open_url,         "label": "Открытие URL"},
    "hotkey":           {"handler": cmd_hotkey,           "label": "Горячие клавиши"},
    "run_macro":        {"handler": cmd_run_macro,        "label": "Запуск макроса"},
    "list_macros":      {"handler": cmd_list_macros,      "label": "Список макросов"},
}
