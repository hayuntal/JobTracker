"""CLI helpers with real logic of their own."""
import pytest

from jobtracker.cli import _await_first_message, _resolve_sources


class FakeBot:
    """Returns the queued responses in order, recording each poll's arguments."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, method, payload=None, timeout=None):
        self.calls.append((method, payload, timeout))
        return self.responses.pop(0) if self.responses else []


def test_wait_returns_as_soon_as_a_message_arrives():
    bot = FakeBot([[], [], [{"update_id": 1}]])
    assert _await_first_message(bot, "https://t.me/x", 60) == [{"update_id": 1}]
    assert len(bot.calls) == 3


def test_wait_gives_the_request_more_time_than_the_poll_window():
    """A read timeout shorter than the poll window would abort every poll."""
    bot = FakeBot([[{"update_id": 1}]])
    _await_first_message(bot, "https://t.me/x", 60)

    _, payload, request_timeout = bot.calls[0]
    assert request_timeout > payload["timeout"]


def test_wait_gives_up_at_the_deadline():
    bot = FakeBot([])                      # never returns anything
    assert _await_first_message(bot, "https://t.me/x", 0) == []
    assert bot.calls == []                 # no point polling with no time left


# -- source resolution ------------------------------------------------------

class Args:
    def __init__(self, only=None, enable=None):
        self.only, self.enable = only, enable


def test_defaults_are_the_two_working_sources():
    assert _resolve_sources(Args()) == ["careers", "linkedin"]


def test_only_overrides_the_defaults():
    assert _resolve_sources(Args(only="careers")) == ["careers"]


def test_enable_adds_opt_in_sources():
    assert _resolve_sources(Args(enable="glassdoor")) == [
        "careers", "linkedin", "glassdoor"]


def test_an_unknown_source_exits_rather_than_silently_doing_nothing():
    with pytest.raises(SystemExit, match="drushim"):
        _resolve_sources(Args(only="drushim"))
