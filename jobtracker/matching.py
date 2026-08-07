"""Decide whether a posting is a job worth sending to WhatsApp.

Three independent gates, all of which must pass:
    title  -> matches one of the configured role groups
    exclude-> does not hit an exclusion / optional seniority / agency rule
    location -> resolves to Israel, or to remote-open-to-Israel

Kept deliberately free of I/O so it is cheap to unit-test.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import Job

# Hebrew letters plus ASCII word chars count as "word" characters. Python's \b
# does the right thing for Hebrew here because Hebrew letters are \w, but the
# ASCII-only boundary trick breaks on mixed strings, so we build explicit
# boundaries that treat any non-letter/digit as a separator.
_BOUNDARY = r"(?<![\w֐-׿]){}(?![\w֐-׿])"

SENIORITY_PATTERNS = [
    ("intern", r"intern|student|סטודנט"),
    ("junior", r"junior|entry.?level|jr\.?|ג'?וניור"),
    ("senior", r"senior|sr\.?|סניור"),
    ("lead", r"\blead\b|team lead|tech lead"),
    ("staff", r"\bstaff\b"),
    ("principal", r"principal"),
    ("manager", r"manager|מנהל"),
    ("director", r"director|head of|\bvp\b|vice president|chief"),
]


def _compile_phrase(phrase: str) -> "re.Pattern":
    """Turn a config phrase into a whole-word, whitespace-tolerant regex."""
    parts = [re.escape(tok) for tok in phrase.lower().split()]
    body = r"[\s\-_/]+".join(parts)
    return re.compile(_BOUNDARY.format(body), re.IGNORECASE | re.UNICODE)


class Matcher:
    def __init__(self, search_config: Dict[str, Any]):
        cfg = search_config
        self.roles = {}
        for role_key, role in (cfg.get("roles") or {}).items():
            self.roles[role_key] = {
                "label": role.get("label", role_key),
                # Longest patterns first so "senior data analyst" wins over
                # "data analyst" and the alert shows the most specific match.
                "patterns": sorted(role.get("patterns") or [], key=len, reverse=True),
                "compiled": [(p, _compile_phrase(p))
                             for p in sorted(role.get("patterns") or [], key=len, reverse=True)],
            }

        self.exclude = [_compile_phrase(p) for p in (cfg.get("exclude_titles") or [])]

        sen = cfg.get("seniority_filter") or {}
        self.seniority_enabled = bool(sen.get("enabled"))
        self.seniority_exclude = [str(x).lower() for x in (sen.get("exclude_levels") or [])]

        agency = cfg.get("agency_filter") or {}
        self.agency_enabled = bool(agency.get("enabled"))
        self.agencies = [_compile_phrase(a) for a in (agency.get("agencies") or [])]

        loc = cfg.get("location") or {}
        self.israel_terms = [_compile_phrase(t) for t in (loc.get("israel_terms") or [])]
        self.include_remote = bool(loc.get("include_remote", True))
        self.remote_terms = [_compile_phrase(t) for t in (loc.get("remote_terms") or [])]
        self.remote_ok = [_compile_phrase(t) for t in (loc.get("remote_ok_regions") or [])]
        self.remote_reject = [_compile_phrase(t) for t in (loc.get("remote_reject_regions") or [])]

    # -- individual gates ---------------------------------------------------

    def match_title(self, title: str) -> Tuple[Optional[str], Optional[str]]:
        """Return (role_key, matched_pattern) or (None, None)."""
        for role_key, role in self.roles.items():
            for phrase, rx in role["compiled"]:
                if rx.search(title or ""):
                    return role_key, phrase
        return None, None

    def is_excluded(self, job: Job) -> Optional[str]:
        """Return a human-readable reason the job is rejected, or None."""
        title = job.title or ""
        for rx in self.exclude:
            m = rx.search(title)
            if m:
                return "excluded title: '%s'" % m.group(0)

        if self.seniority_enabled:
            level = detect_seniority(title)
            if level and level in self.seniority_exclude:
                return "seniority filtered: %s" % level
            for bad in self.seniority_exclude:
                if _compile_phrase(bad).search(title):
                    return "seniority filtered: %s" % bad

        if self.agency_enabled:
            haystack = "%s %s" % (job.company or "", job.description or "")
            for rx in self.agencies:
                m = rx.search(haystack)
                if m:
                    return "recruiting agency: '%s'" % m.group(0)
        return None

    def match_location(self, location: str, description: str = "") -> Tuple[bool, bool]:
        """Return (is_israel_eligible, is_remote)."""
        loc = location or ""
        is_remote = any(rx.search(loc) for rx in self.remote_terms)

        if any(rx.search(loc) for rx in self.israel_terms):
            return True, is_remote

        if not (self.include_remote and is_remote):
            return False, is_remote

        # Remote posting with no Israeli city named. Accept it only if it is
        # scoped to a region that includes Israel — or scoped to nothing at all.
        if any(rx.search(loc) for rx in self.remote_reject):
            return False, True
        if any(rx.search(loc) for rx in self.remote_ok):
            return True, True

        # Bare "Remote" with no region: check the description before trusting it,
        # since most bare-remote roles turn out to be US-scoped.
        desc = (description or "")[:2000]
        if any(rx.search(desc) for rx in self.israel_terms):
            return True, True
        if any(rx.search(desc) for rx in self.remote_reject):
            return False, True
        return False, True

    def needs_description(self, job: Job) -> bool:
        """True when the location alone can't decide and the body would help.

        That's the bare-"Remote" case: no Israeli city, no region qualifier
        either way. Lets the pipeline fetch descriptions for just those jobs.
        """
        loc = job.location or ""
        if job.description:
            return False
        if any(rx.search(loc) for rx in self.israel_terms):
            return False
        if not (self.include_remote and any(rx.search(loc) for rx in self.remote_terms)):
            return False
        if any(rx.search(loc) for rx in self.remote_reject):
            return False
        return not any(rx.search(loc) for rx in self.remote_ok)

    # -- top-level ----------------------------------------------------------

    def evaluate(self, job: Job) -> Tuple[bool, Optional[str]]:
        """Run every gate. Returns (accepted, reason_if_rejected).

        On acceptance the job is annotated in place with the matched role,
        matched pattern, detected seniority and remote flag.
        """
        role_key, pattern = self.match_title(job.title)
        if not role_key:
            return False, "title did not match any target role"

        reason = self.is_excluded(job)
        if reason:
            return False, reason

        in_israel, is_remote = self.match_location(job.location, job.description)
        if not in_israel:
            return False, "location not in Israel: '%s'" % (job.location or "unknown")

        job.matched_role = self.roles[role_key]["label"]
        job.matched_pattern = pattern
        job.seniority = detect_seniority(job.title)
        job.is_remote = is_remote
        return True, None

    def filter_jobs(self, jobs: List[Job]) -> List[Job]:
        return [j for j in jobs if self.evaluate(j)[0]]


def detect_seniority(title: str) -> Optional[str]:
    """Best-effort seniority label, used to tag alerts."""
    text = (title or "").lower()
    for level, pattern in SENIORITY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE | re.UNICODE):
            return level
    return None
