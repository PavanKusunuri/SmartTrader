"""Optional Telegram notifier. DISABLED unless explicitly enabled.

Enable by setting, in the git-ignored local .env (never in chat or source):
    TELEGRAM_ENABLED=true
    TELEGRAM_BOT_TOKEN=<from @BotFather>
    TELEGRAM_CHAT_ID=<your chat id>

The bot token is part of the request URL (Telegram's API design), so this module
never logs or re-raises transport errors verbatim: failures are reported by
exception type only. Outbound traffic is one HTTPS POST per alert to
api.telegram.org containing the alert text; no credentials or account data.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable

import httpx
from pydantic import SecretStr

from app.alerts.base import Alert, Notifier, NullNotifier

log = logging.getLogger("smart_trader.alerts")
_API = "https://api.telegram.org"


class TelegramNotifier(Notifier):
    def __init__(
        self, bot_token: SecretStr, chat_id: str, *, timeout: float = 10.0, max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._max_retries = max_retries
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)
        self.last_error: str | None = None

    def __repr__(self) -> str:
        return "TelegramNotifier(token=**********)"

    def send(self, alert: Alert) -> bool:
        url = f"{_API}/bot{self._token.get_secret_value()}/sendMessage"
        body = {"chat_id": self._chat_id, "text": alert.render(), "disable_web_page_preview": True}
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.post(url, json=body)
            except httpx.HTTPError as exc:
                self.last_error = f"transport error: {type(exc).__name__}"
            else:
                if resp.status_code == 200:
                    self.last_error = None
                    return True
                self.last_error = f"telegram HTTP {resp.status_code}"
                if resp.status_code == 429:
                    try:
                        delay = float(resp.json().get("parameters", {}).get("retry_after", 1))
                    except (ValueError, TypeError, AttributeError):
                        delay = 1.0
                    if attempt < self._max_retries:
                        self._sleep(min(delay, 5.0))
                    continue
                if resp.status_code < 500:
                    break  # a 4xx other than 429 will not improve by retrying
            if attempt < self._max_retries:
                self._sleep(min(2.0 ** attempt, 5.0))
        log.warning("telegram alert not delivered: %s", self.last_error)
        return False


def load_notifier(env_file: str | Path | None = ".env") -> Notifier:
    """Telegram notifier if (and only if) fully enabled and configured, else a no-op."""
    if env_file is not None and Path(env_file).is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    enabled = os.environ.get("TELEGRAM_ENABLED", "").strip().lower() in ("1", "true", "yes")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (enabled and token and chat):
        return NullNotifier()
    return TelegramNotifier(SecretStr(token), chat)
