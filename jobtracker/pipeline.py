"""Wiring: build sources → collect → match → de-duplicate → notify → persist."""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import STATE_DIR, Settings, load_companies, load_search
from .matching import Matcher
from .models import Job, SourceResult
from .notify import (CallMeBotNotifier, ConsoleNotifier, Notifier,
                     TelegramNotifier, build_digest)
from .sources import (ATSSource, FacebookGroupsSource, GlassdoorSource,
                      LinkedInSource)
from .sources.ats import enrich_descriptions
from .store import SeenStore

log = logging.getLogger(__name__)

STATE_FILE = STATE_DIR / "seen_jobs.json"

# Fallback search terms, used only if config/search.yaml has no
# `search_queries` block. Kept short on purpose: every extra term is another
# round of throttled requests against sites that rate-limit.
DEFAULT_QUERIES = [
    "Data Engineer",
    "Data Analyst",
    "Data Scientist",
]


def search_queries() -> List[str]:
    """Search terms for the keyword-driven sources (LinkedIn, Glassdoor).

    The ATS sources need no queries at all — they return every open role a
    company has and the matcher filters locally. LinkedIn has no such endpoint,
    so it has to be *asked* for specific terms.

    These live in config/search.yaml next to the role patterns, but stay a
    separate list on purpose: a search term wants to be broad ("Data Analyst",
    which LinkedIn expands to senior/junior/product variants), while a role
    pattern can be narrow ("senior product analyst"). Feeding every pattern to
    LinkedIn would mean dozens of throttled requests for no extra coverage.
    """
    configured = load_search().get("search_queries")
    return [str(q) for q in configured if str(q).strip()] if configured else list(DEFAULT_QUERIES)


@dataclass
class RunReport:
    results: List[SourceResult] = field(default_factory=list)
    collected: int = 0
    matched: int = 0
    new: int = 0
    sent: int = 0          # included in this digest
    deferred: int = 0      # over the cap; held back for the next run
    notified: bool = False
    notify_error: Optional[str] = None
    digest: str = ""
    new_jobs: List[Job] = field(default_factory=list)
    sent_jobs: List[Job] = field(default_factory=list)

    @property
    def failed_sources(self) -> List[SourceResult]:
        return [r for r in self.results if not r.ok]


def build_sources(settings: Settings, enabled: List[str]) -> List:
    """Instantiate every requested source.

    `enabled` holds source names; sources not listed are skipped entirely so we
    don't waste requests on them.
    """
    companies = load_companies()
    queries = search_queries()
    out = []

    if "careers" in enabled:
        out.append(ATSSource(companies))
    if "linkedin" in enabled:
        out.append(LinkedInSource(queries))
    if "glassdoor" in enabled:
        out.append(GlassdoorSource(queries[:2]))
    if "facebook" in enabled:
        out.append(FacebookGroupsSource(settings.facebook_cookie, queries[:2]))
    return out


def pick_notifier(settings: Settings, dry_run: bool) -> Notifier:
    """Telegram first, CallMeBot/WhatsApp as a fallback, console as a last resort.

    Falling back to the console rather than raising is deliberate: a run that
    still reports its findings to stdout is useful, and `run` exits non-zero on
    a genuine delivery *failure* — only missing configuration lands here.
    """
    if dry_run:
        return ConsoleNotifier()

    candidates = [
        TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id),
        CallMeBotNotifier(settings.callmebot_phone, settings.callmebot_apikey),
    ]
    for notifier in candidates:
        if notifier.available():
            return notifier

    log.warning("No delivery channel configured (%s) — printing to console instead",
                candidates[0].unavailable_reason())
    return ConsoleNotifier()


def run(enabled: List[str], dry_run: bool = False,
        settings: Optional[Settings] = None, notify: bool = True) -> RunReport:
    settings = settings or Settings.from_env()
    matcher = Matcher(load_search())
    store = SeenStore(STATE_FILE)
    report = RunReport()

    # 1. Collect from every source. Failures are recorded, never raised.
    all_jobs: List[Job] = []
    for source in build_sources(settings, enabled):
        result = source.run()
        report.results.append(result)
        all_jobs.extend(result.jobs)
        log.info("%-14s %-7s %4d jobs  (%.1fs)%s", result.source, result.status,
                 len(result.jobs), result.duration_s,
                 "  " + result.error if result.error else "")
    report.collected = len(all_jobs)

    # 2. Title gate first — it's pure string work and throws away ~98% of the
    #    volume, so everything after it stays cheap.
    title_ok = [j for j in all_jobs if matcher.match_title(j.title)[0]]

    #    Only now fetch descriptions, and only for the survivors whose location
    #    is too vague to judge (bare "Remote" and similar).
    ambiguous = [j for j in title_ok if matcher.needs_description(j)]
    if ambiguous:
        fetched = enrich_descriptions(ambiguous)
        log.info("Fetched %d/%d descriptions to resolve ambiguous locations",
                 fetched, len(ambiguous))

    # 3. Full evaluation: exclusions + location.
    matched = matcher.filter_jobs(title_ok)
    report.matched = len(matched)

    # 4. Drop anything already sent in a previous run.
    new_jobs = store.new_jobs(matched)
    report.new = len(new_jobs)
    report.new_jobs = new_jobs

    if not new_jobs:
        log.info("Nothing new (%d collected, %d matched, 0 new)",
                 report.collected, report.matched)
        return report

    # `seed` wants the full set of current matches without a digest being built
    # or sent — it records them itself.
    if not notify:
        log.info("Notification skipped (%d new jobs identified)", report.new)
        return report

    # 5. Notify. The digest is capped, and only the jobs actually in it are
    #    marked as seen — anything over the cap is deferred to the next run
    #    rather than being silently dropped. A backlog therefore drains at
    #    `digest_max_jobs` per run instead of being lost.
    notifier = pick_notifier(settings, dry_run)
    cap = settings.digest_max_jobs
    sent_jobs = new_jobs[:cap] if cap > 0 else list(new_jobs)
    report.sent_jobs = sent_jobs
    report.sent = len(sent_jobs)
    report.deferred = len(new_jobs) - len(sent_jobs)
    report.digest = build_digest(sent_jobs, remaining=report.deferred,
                                 style=notifier.digest_style)
    try:
        notifier.send(report.digest)
        report.notified = True
    except Exception as exc:  # noqa: BLE001
        # Deliberately do NOT mark jobs as seen if delivery failed — otherwise a
        # transient outage would silently swallow those jobs forever.
        report.notify_error = "%s: %s" % (type(exc).__name__, exc)
        log.error("Notification failed, jobs left unmarked for retry: %s", exc)
        return report

    # 6. Persist only after a confirmed send.
    if not dry_run:
        store.mark_seen(sent_jobs)
        store.prune()
        store.save()
        log.info("State saved (%d fingerprints tracked)", len(store))
    return report


def source_health(enabled: List[str], settings: Optional[Settings] = None) -> Dict:
    """Poll every source once and report what actually works, without notifying."""
    settings = settings or Settings.from_env()
    matcher = Matcher(load_search())
    out = {"sources": [], "companies": {}}
    for source in build_sources(settings, enabled):
        result = source.run()
        israeli = sum(1 for j in result.jobs if matcher.match_location(j.location, j.description)[0])
        matched = len(matcher.filter_jobs(list(result.jobs)))
        out["sources"].append({
            "name": result.source,
            "ok": result.ok,
            "error": result.error,
            "jobs": len(result.jobs),
            "israeli": israeli,
            "matched": matched,
            "duration_s": round(result.duration_s, 1),
        })
        if isinstance(source, ATSSource):
            out["companies"] = source.company_results
    return out
