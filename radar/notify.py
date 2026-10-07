"""Alert delivery: console, log file and Telegram."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping

import requests

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_LIMIT = 4000


def telegram_credentials() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return (token, chat_id) if token and chat_id else None


def send_telegram(text: str, token: str, chat_id: str) -> bool:
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": text[:TELEGRAM_LIMIT], "disable_web_page_preview": True},
            timeout=20,
        )
        if not resp.ok:
            log.warning("Telegram respondio %s: %s", resp.status_code, resp.text[:200])
        return resp.ok
    except requests.RequestException as exc:
        # Never log the URL: it contains the bot token.
        log.warning("Telegram no disponible: %s", type(exc).__name__)
        return False


def telegram_chat_ids(token: str) -> list[dict[str, Any]]:
    """Chats that have written to the bot (used once to find TELEGRAM_CHAT_ID)."""
    resp = requests.get(TELEGRAM_API.format(token=token, method="getUpdates"), timeout=20)
    resp.raise_for_status()
    chats: dict[int, dict[str, Any]] = {}
    for update in resp.json().get("result", []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = {
                "chat_id": chat["id"],
                "tipo": chat.get("type"),
                "nombre": chat.get("username") or chat.get("title") or chat.get("first_name"),
            }
    return list(chats.values())


def append_log(path: str, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text + "\n" + "-" * 60 + "\n")


def deliver(text: str, cfg: Mapping[str, Any]) -> list[str]:
    """Send an alert through every enabled channel. Returns channels used."""
    channels = []
    append_log(cfg["log_file"], text)
    channels.append("log")
    if cfg["notify"].get("console", True):
        print("\n" + text + "\n", flush=True)
        channels.append("consola")
    creds = telegram_credentials() if cfg["notify"].get("telegram", True) else None
    if creds and send_telegram(text, *creds):
        channels.append("telegram")
    return channels
