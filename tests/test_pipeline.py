"""End-to-end wiring, with fake sources and a fake notifier.

These cover the rules that decide whether a job is lost forever:
delivery failure must not mark anything seen, and the digest cap must defer
rather than drop.
"""
import pytest

from jobtracker import pipeline
from jobtracker.config import Settings
from jobtracker.models import Job, SourceResult
from jobtracker.notify.base import Notifier
from jobtracker.store import SeenStore


class FakeSource:
    def __init__(self, jobs, ok=True, error=None):
        self._result = SourceResult(source="fake", jobs=jobs, ok=ok, error=error)

    def run(self):
        return self._result


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def send(self, message):
        if self.fail:
            raise RuntimeError("delivery exploded")
        self.messages.append(message)


def make_jobs(n, title="Data Engineer"):
    return [Job(title=title, company="Company %d" % i, location="Tel Aviv",
                url="https://example.com/%d" % i, source="greenhouse")
            for i in range(n)]


def settings(max_jobs=12):
    return Settings(telegram_bot_token="", telegram_chat_id="",
                    callmebot_phone="", callmebot_apikey="",
                    facebook_cookie="", digest_max_jobs=max_jobs)


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Point the pipeline at a temp state file and swap in fakes."""
    monkeypatch.setattr(pipeline, "STATE_FILE", tmp_path / "seen.json")

    def _wire(jobs, notifier=None, max_jobs=12):
        notifier = notifier or RecordingNotifier()
        monkeypatch.setattr(pipeline, "build_sources",
                            lambda s, e: [FakeSource(jobs)])
        monkeypatch.setattr(pipeline, "pick_notifier", lambda s, d: notifier)
        report = pipeline.run(["careers"], settings=settings(max_jobs))
        return report, notifier, SeenStore(tmp_path / "seen.json")

    return _wire


def test_matching_jobs_are_delivered_and_recorded(wire):
    report, notifier, store = wire(make_jobs(3))

    assert report.collected == 3
    assert report.matched == 3
    assert report.new == 3
    assert report.notified
    assert len(notifier.messages) == 1
    assert len(store) == 3


def test_non_matching_jobs_never_reach_the_notifier(wire):
    report, notifier, store = wire(make_jobs(3, title="Chef de Partie"))

    assert report.collected == 3
    assert report.matched == 0
    assert notifier.messages == []
    assert len(store) == 0


def test_second_run_is_silent(wire, monkeypatch, tmp_path):
    wire(make_jobs(2))
    report, notifier, _ = wire(make_jobs(2))

    assert report.new == 0
    assert notifier.messages == []


def test_jobs_over_the_cap_are_deferred_not_dropped(wire):
    """The bug this guards: capped jobs used to be marked seen and never sent."""
    report, notifier, store = wire(make_jobs(20), max_jobs=5)

    assert report.new == 20
    assert report.sent == 5
    assert report.deferred == 15
    assert len(store) == 5                     # only the delivered five
    assert "15 more queued" in notifier.messages[0]


def test_a_deferred_backlog_drains_over_later_runs(wire):
    wire(make_jobs(20), max_jobs=5)
    report, _, store = wire(make_jobs(20), max_jobs=5)

    assert report.new == 15                    # the ones held back before
    assert report.sent == 5
    assert len(store) == 10


def test_cap_of_zero_means_no_cap(wire):
    report, _, store = wire(make_jobs(30), max_jobs=0)

    assert report.sent == 30
    assert report.deferred == 0
    assert len(store) == 30


def test_delivery_failure_leaves_everything_unseen(wire):
    """A failed send must be retried in full, not silently swallowed."""
    report, _, store = wire(make_jobs(3), notifier=RecordingNotifier(fail=True))

    assert report.notify_error and "delivery exploded" in report.notify_error
    assert not report.notified
    assert len(store) == 0


def test_notify_false_finds_jobs_but_sends_nothing(monkeypatch, tmp_path):
    """What `seed` relies on: the full match list, no digest, no delivery."""
    monkeypatch.setattr(pipeline, "STATE_FILE", tmp_path / "seen.json")
    monkeypatch.setattr(pipeline, "build_sources",
                        lambda s, e: [FakeSource(make_jobs(20))])
    notifier = RecordingNotifier()
    monkeypatch.setattr(pipeline, "pick_notifier", lambda s, d: notifier)

    report = pipeline.run(["careers"], dry_run=True, notify=False,
                          settings=settings(max_jobs=5))

    assert len(report.new_jobs) == 20      # not truncated to the cap
    assert report.digest == ""
    assert notifier.messages == []


def test_dry_run_changes_no_state(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "STATE_FILE", tmp_path / "seen.json")
    monkeypatch.setattr(pipeline, "build_sources",
                        lambda s, e: [FakeSource(make_jobs(3))])
    notifier = RecordingNotifier()
    monkeypatch.setattr(pipeline, "pick_notifier", lambda s, d: notifier)

    report = pipeline.run(["careers"], dry_run=True, settings=settings())

    assert report.new == 3
    assert not (tmp_path / "seen.json").exists()


def test_a_failed_source_does_not_sink_the_run(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "STATE_FILE", tmp_path / "seen.json")
    monkeypatch.setattr(pipeline, "build_sources", lambda s, e: [
        FakeSource([], ok=False, error="ConnectionError: boom"),
        FakeSource(make_jobs(2)),
    ])
    notifier = RecordingNotifier()
    monkeypatch.setattr(pipeline, "pick_notifier", lambda s, d: notifier)

    report = pipeline.run(["careers", "linkedin"], settings=settings())

    assert len(report.failed_sources) == 1
    assert report.new == 2
    assert report.notified


# -- search terms -----------------------------------------------------------

def test_search_queries_come_from_config(monkeypatch):
    monkeypatch.setattr(pipeline, "load_search",
                        lambda: {"search_queries": ["Data Architect", "MLOps"]})
    assert pipeline.search_queries() == ["Data Architect", "MLOps"]


def test_search_queries_fall_back_when_unconfigured(monkeypatch):
    monkeypatch.setattr(pipeline, "load_search", lambda: {})
    assert pipeline.search_queries() == pipeline.DEFAULT_QUERIES


def test_blank_search_terms_are_dropped(monkeypatch):
    """An empty term would ask LinkedIn for everything."""
    monkeypatch.setattr(pipeline, "load_search",
                        lambda: {"search_queries": ["Data Engineer", "", "  "]})
    assert pipeline.search_queries() == ["Data Engineer"]


def test_linkedin_is_built_with_the_configured_terms(monkeypatch):
    monkeypatch.setattr(pipeline, "load_search",
                        lambda: {"search_queries": ["Data Architect"]})
    monkeypatch.setattr(pipeline, "load_companies", lambda: [])
    sources = pipeline.build_sources(settings(), ["linkedin"])
    assert sources[0].queries == ["Data Architect"]


def test_the_shipped_config_actually_parses():
    """Guards a YAML typo in search_queries silently reverting to defaults."""
    from jobtracker.config import load_search
    assert load_search().get("search_queries")


# -- notifier selection -----------------------------------------------------

def test_telegram_is_preferred_over_whatsapp():
    s = Settings(telegram_bot_token="1:A", telegram_chat_id="5",
                 callmebot_phone="972500000000", callmebot_apikey="k",
                 facebook_cookie="", digest_max_jobs=12)
    assert pipeline.pick_notifier(s, dry_run=False).name == "telegram"


def test_whatsapp_is_used_when_telegram_is_unset():
    s = Settings(telegram_bot_token="", telegram_chat_id="",
                 callmebot_phone="972500000000", callmebot_apikey="k",
                 facebook_cookie="", digest_max_jobs=12)
    assert pipeline.pick_notifier(s, dry_run=False).name == "whatsapp"


def test_console_when_nothing_is_configured():
    assert pipeline.pick_notifier(settings(), dry_run=False).name == "console"


def test_dry_run_always_uses_the_console():
    s = Settings(telegram_bot_token="1:A", telegram_chat_id="5",
                 callmebot_phone="", callmebot_apikey="",
                 facebook_cookie="", digest_max_jobs=12)
    assert pipeline.pick_notifier(s, dry_run=True).name == "console"
