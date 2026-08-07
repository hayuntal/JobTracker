"""Digest rendering and Telegram delivery.

Nothing here touches the network: `http.post_json` is replaced with a recorder.
"""
import pytest

from jobtracker import http
from jobtracker.models import Job
from jobtracker.notify import build_digest, build_summary
from jobtracker.notify.telegram import MAX_CHARS, TelegramNotifier


def job(title="Data Engineer", company="Wiz", location="Tel Aviv",
        url="https://example.com/job/1", role="Data Engineer",
        seniority=None, remote=False):
    j = Job(title=title, company=company, location=location, url=url, source="greenhouse")
    j.matched_role = role
    j.seniority = seniority
    j.is_remote = remote
    return j


# -- digest -----------------------------------------------------------------

def test_empty_digest_is_empty():
    assert build_digest([]) == ""


def test_plain_digest_contains_the_essentials():
    text = build_digest([job(seniority="senior")])
    assert "*1 new job*" in text
    assert "Data Engineer — Wiz" in text
    assert "[senior]" in text
    assert "https://example.com/job/1" in text


def test_singular_and_plural_header():
    assert "*1 new job*" in build_digest([job()])
    assert "*2 new jobs*" in build_digest([job(), job(title="Data Analyst")])


def test_jobs_are_grouped_by_role():
    text = build_digest([job(), job(title="Data Analyst", role="Senior Data Analyst")])
    assert "*Data Engineer* (1)" in text
    assert "*Senior Data Analyst* (1)" in text


def test_remaining_is_reported_not_hidden():
    text = build_digest([job()], remaining=7)
    assert "7 more queued" in text


def test_no_footer_when_nothing_is_deferred():
    assert "queued" not in build_digest([job()])


# -- telegram rendering -----------------------------------------------------

def test_telegram_digest_links_the_title():
    text = build_digest([job()], style="telegram")
    assert '<a href="https://example.com/job/1">Data Engineer</a>' in text
    # The URL is the link target, so it is never also printed as a bare line.
    assert "\n  https://example.com/job/1" not in text


def test_telegram_escapes_html_in_untrusted_fields():
    """An unescaped '&' in a title would make Telegram reject the whole message."""
    text = build_digest([job(title="Data Engineer <R&D>", company="A & B")],
                        style="telegram")
    assert "Data Engineer &lt;R&amp;D&gt;" in text
    assert "A &amp; B" in text
    assert "<R&D>" not in text


def test_plain_style_does_not_escape():
    assert "A & B" in build_digest([job(company="A & B")])


def test_summary_line():
    assert build_summary([]) == "No new matching jobs."
    assert "1 new job(s)" in build_summary([job()])


# -- telegram delivery ------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def recorder(monkeypatch):
    calls = []

    def fake_post(url, payload, **kwargs):
        calls.append((url, payload))
        return FakeResponse({"ok": True, "result": {"message_id": len(calls)}})

    monkeypatch.setattr(http, "post_json", fake_post)
    return calls


def test_send_posts_html_with_previews_off(recorder):
    TelegramNotifier("123:ABC", "555").send("<b>hi</b>")
    (url, payload), = recorder
    assert url.endswith("/sendMessage")
    assert payload["chat_id"] == "555"
    assert payload["parse_mode"] == "HTML"
    assert payload["disable_web_page_preview"] is True


def test_long_message_is_split_on_line_boundaries(recorder):
    message = "\n".join("• Job number %d — Company" % i for i in range(400))
    TelegramNotifier("123:ABC", "555").send(message)

    assert len(recorder) > 1
    for _, payload in recorder:
        assert len(payload["text"]) <= MAX_CHARS + 40      # + the "(1/3)\n" prefix
    # No entry was cut in half.
    rejoined = "".join(p["text"].split("\n", 1)[1] for _, p in recorder)
    assert "• Job number 399 — Company" in rejoined


def test_short_message_is_not_chunked(recorder):
    TelegramNotifier("123:ABC", "555").send("short")
    assert len(recorder) == 1
    assert recorder[0][1]["text"] == "short"


def test_api_level_failure_raises(monkeypatch):
    monkeypatch.setattr(http, "post_json",
                        lambda url, payload, **kw: FakeResponse(
                            {"ok": False, "description": "chat not found"}))
    with pytest.raises(RuntimeError, match="chat not found"):
        TelegramNotifier("123:ABC", "555").send("hi")


def test_the_bot_token_never_appears_in_an_error(monkeypatch):
    """Errors reach GitHub Actions logs, and requests puts the URL in them."""
    secret = "8123456789:AAHsuperSecretTokenValue"

    def boom(url, payload, **kwargs):
        raise ValueError("connection failed for url: %s" % url)

    monkeypatch.setattr(http, "post_json", boom)
    with pytest.raises(RuntimeError) as excinfo:
        TelegramNotifier(secret, "555").send("hi")
    assert secret not in str(excinfo.value)
    assert "/bot***/" in str(excinfo.value)


def test_availability_reporting():
    assert TelegramNotifier("123:ABC", "555").available()
    assert not TelegramNotifier("", "555").available()
    assert "TELEGRAM_BOT_TOKEN" in TelegramNotifier("", "555").unavailable_reason()
    assert "TELEGRAM_CHAT_ID" in TelegramNotifier("123:ABC", "").unavailable_reason()
