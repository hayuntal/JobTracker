"""Prints the digest to stdout. Used by --dry-run and as a fallback."""
from .base import Notifier


class ConsoleNotifier(Notifier):
    name = "console"

    def send(self, message: str) -> None:
        print("\n" + "=" * 62)
        print(message)
        print("=" * 62 + "\n")
