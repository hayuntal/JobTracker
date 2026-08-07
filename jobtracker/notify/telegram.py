"""Telegram delivery through the Bot API.

Chosen over WhatsApp relays because it is the one channel a scheduled job can
use reliably: no 24-hour messaging window, no template approval, no session that
silently expires. A bot token plus your chat id is the whole setup.

The bot token lives in the request *path*, so every error and log line that
mentions a URL goes through `http.redact` first.
"""
import logging
from typing import Any, Dict, List

from .. import http
from .base import Notifier

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot%s/%s"

# Telegram's hard limit is 4096 characters. Leave headroom for the "(1/3)"
# prefix added when a digest has to span several messages.
MAX_CHARS = 3800


class TelegramNotifier(Notifier):
    name = "telegram"
    digest_style = "telegram"

    def __init__(self, token: str, chat_id: str):
        self.token = (token or "").strip()
        self.chat_id = (chat_id or "").strip()

    def available(self) -> bool:
        return bool(self.token and self.chat_id)

    def unavailable_reason(self) -> str:
        missing = [name for name, value in (
            ("TELEGRAM_BOT_TOKEN", self.token),
            ("TELEGRAM_CHAT_ID", self.chat_id),
        ) if not value]
        return ("%s not set — run `python -m jobtracker setup-telegram`"
                % " and ".join(missing))

    def send(self, message: str) -> None:
        for chunk in self._chunk(message):
            self.call("sendMessage", {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                # Job links would otherwise each append a big preview card and
                # bury the list itself.
                "disable_web_page_preview": True,
            })
            log.info("Telegram message sent (%d chars)", len(chunk))

    # -- raw API ------------------------------------------------------------

    def call(self, method: str, payload: Dict[str, Any] = None,
             timeout: int = None) -> Any:
        """Invoke a Bot API method and return its `result`.

        Telegram signals failure in the body (`ok: false`) as often as by status
        code, so both are checked. Any error text is redacted before it escapes:
        `requests` embeds the full URL — including the token — in its exception
        messages, and this text ends up in CI logs.

        `timeout` overrides the HTTP read timeout, which long-polling needs: the
        request itself must outlive the poll window the API is holding open.
        """
        url = API % (self.token, method)
        kwargs = {"timeout": timeout} if timeout else {}
        try:
            resp = http.post_json(url, payload or {}, **kwargs)
        except Exception as exc:  # noqa: BLE001 - re-raised scrubbed
            raise RuntimeError("Telegram %s failed: %s"
                               % (method, http.redact(str(exc)))) from None

        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("Telegram %s returned non-JSON (HTTP %d): %s"
                               % (method, resp.status_code,
                                  http.redact((resp.text or "")[:200]))) from None

        if not data.get("ok"):
            raise RuntimeError("Telegram %s rejected (HTTP %d): %s"
                               % (method, resp.status_code,
                                  http.redact(str(data.get("description", data))[:300])))
        return data.get("result")

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _chunk(message: str) -> List[str]:
        """Split on line boundaries so a job entry is never cut in half.

        Splitting mid-entry would also risk breaking an HTML tag across two
        messages, which Telegram rejects outright.
        """
        if len(message) <= MAX_CHARS:
            return [message]
        chunks, current = [], ""
        for line in message.split("\n"):
            if len(current) + len(line) + 1 > MAX_CHARS and current:
                chunks.append(current.rstrip())
                current = ""
            current += line + "\n"
        if current.strip():
            chunks.append(current.rstrip())
        total = len(chunks)
        return ["(%d/%d)\n%s" % (i + 1, total, c) for i, c in enumerate(chunks)]
