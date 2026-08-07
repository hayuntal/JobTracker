"""Wiring: build sources → collect → match → de-duplicate → notify → persist."""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import STATE_DIR, Settings, load_companies, load_search
from .matching import Matcher
from .models import Job, SourceResult
from .notify import CallMeBotNotifier, ConsoleNotifier, Notifier, build_digest
from .sources import (AllJobsSource, ATSSource, DrushimSource,
                      FacebookGroupsSource, GlassdoorSource, JobMasterSource,
                      LinkedInSource)
from .sources.ats import enrich_descriptions
from .store import SeenStore

log = logging.getLogger(__name__)

STATE_FILE = STATE_DIR / "seen_jobs.json"

# Keyword-driven sources (LinkedIn, boards) need search strings rather than a
# company list. Kept short on purpose: every extra term is another round of
# throttled requests against sites that rate-limit.
DEFAULT_QUERIES = [
    "Data Engineer",
    "Data Analyst",
    "Data Scientist",
    "Analytics Engineer",
    "BI Developer",
]


@dataclass
class RunReport:
    results: List[SourceResult] = field(default_factory=list)
    collected: int = 0
    matched: int = 0
    new: int = 0
    notified: bool = False
    notify_error: Optional[str] = None
    digest: str = ""
    new_jobs: List[Job] = field(default_factory=list)

    @property
    def failed_sources(self) -> List[SourceResult]:
        return [r for r in self.results if not r.ok]


def build_sources(settings: Settings, enabled: List[str]) -> List:
    """Instantiate every requested source.

    `enabled` holds source names; sources not listed are skipped entirely so we
    don't waste requests on them.
    """
    companies = load_companies()
    out = []

    if "careers" in enabled:
        out.append(ATSSource(companies))
    if "linkedin" in enabled:
        out.append(LinkedInSource(DEFAULT_QUERIES[:3]))
    if "drushim" in enabled:
        out.append(DrushimSource(DEFAULT_QUERIES[:3]))
    if "alljobs" in enabled:
        out.append(AllJobsSource(DEFAULT_QUERIES[:3]))
    if "jobmaster" in enabled:
        out.append(JobMasterSource(DEFAULT_QUERIES[:3]))
    if "glassdoor" in enabled:
        out.append(GlassdoorSource(DEFAULT_QUERIES[:2]))
    if "facebook" in enabled:
        out.append(FacebookGroupsSource(settings.facebook_cookie, DEFAULT_QUERIES[:2]))
    return out


def pick_notifier(settings: Settings, dry_run: bool) -> Notifier:
    if dry_run:
        return ConsoleNotifier()
    notifier = CallMeBotNotifier(settings.callmebot_phone, settings.callmebot_apikey)
    if not notifier.available():
        log.warning("WhatsApp not configured (%s) — printing to console instead",
                    notifier.unavailable_reason())
        return ConsoleNotifier()
    return notifier


def run(enabled: List[str], dry_run: bool = False,
        settings: Optional[Settings] = None) -> RunReport:
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

    # 5. Notify.
    report.digest = build_digest(new_jobs, settings.digest_max_jobs)
    notifier = pick_notifier(settings, dry_run)
    try:
        notifier.send(report.digest)
        report.notified = True
    except Exception as exc:  # noqa: BLE001
        # Deliberately do NOT mark jobs as seen if delivery failed — otherwise a
        # transient CallMeBot outage would silently swallow those jobs forever.
        report.notify_error = "%s: %s" % (type(exc).__name__, exc)
        log.error("Notification failed, jobs left unmarked for retry: %s", exc)
        return report

    # 6. Persist only after a confirmed send.
    if not dry_run:
        store.mark_seen(new_jobs)
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
