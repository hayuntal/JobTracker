"""WhatsApp delivery through CallMeBot."""
import logging
import time
from typing import List
from urllib.parse import urlencode

from .. import http
from .base import Notifier

log = logging.getLogger(__name__)

ENDPOINT = "https://api.callmebot.com/whatsapp.php"

# CallMeBot passes the message in a query string, so the practical ceiling is
# URL length rather than WhatsApp's own limit. Stay well under it.
MAX_CHARS = 1200

# The service asks for no more than ~1 message per few seconds.
SEND_GAP_SECONDS = 4.0


class CallMeBotNotifier(Notifier):
    name = "whatsapp"
    digest_style = "plain"      # WhatsApp renders *bold* the same way

    def __init__(self, phone: str, apikey: str):
        self.phone = (phone or "").lstrip("+").replace(" ", "").replace("-", "")
        self.apikey = apikey

    def available(self) -> bool:
        return bool(self.phone and self.apikey)

    def unavailable_reason(self) -> str:
        return "CALLMEBOT_PHONE / CALLMEBOT_APIKEY not set (see .env.example)"

    def send(self, message: str) -> None:
        for i, chunk in enumerate(self._chunk(message)):
            if i:
                time.sleep(SEND_GAP_SECONDS)
            self._send_one(chunk)

    def _send_one(self, text: str) -> None:
        url = "%s?%s" % (ENDPOINT, urlencode({
            "phone": self.phone,
            "text": text,
            "apikey": self.apikey,
        }))
        resp = http.get(url, timeout=30)
        body = (resp.text or "")[:300]
        # CallMeBot answers 200 with an HTML error body on bad credentials, so
        # the status code alone is not enough to call it a success.
        if resp.status_code != 200 or ("apikey" in body.lower() and "invalid" in body.lower()):
            raise RuntimeError("CallMeBot rejected the message (HTTP %d): %s"
                               % (resp.status_code, body))
        log.info("WhatsApp message sent (%d chars)", len(text))

    @staticmethod
    def _chunk(message: str) -> List[str]:
        """Split on line boundaries so a job entry is never cut in half."""
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
