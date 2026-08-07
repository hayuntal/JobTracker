"""Notifier contract — swap WhatsApp for anything else without touching the pipeline."""


class Notifier:
    name = "base"

    #: Which rendering of the digest this channel wants. See notify/format.py.
    digest_style = "plain"

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return "not configured"

    def send(self, message: str) -> None:  # pragma: no cover
        raise NotImplementedError
