"""Core data types shared across sources, matching and notification."""
from dataclasses import dataclass, field
from typing import Optional, List
import hashlib
import re


def _norm(text: str) -> str:
    """Lowercase and collapse whitespace/punctuation, for stable fingerprints."""
    return re.sub(r"[^a-z0-9֐-׿]+", " ", (text or "").lower()).strip()


@dataclass
class Job:
    """One job posting, normalised across every source."""

    title: str
    company: str
    location: str
    url: str
    source: str                      # e.g. "greenhouse", "linkedin"
    description: str = ""
    posted_at: Optional[str] = None  # ISO8601 when the source provides it
    external_id: Optional[str] = None

    #: Where to fetch the full description if it wasn't included in the listing.
    #: Job lists are fetched without descriptions (far faster); the pipeline
    #: back-fills only the few jobs whose location is too vague to judge.
    detail_url: Optional[str] = None

    # Filled in by the matcher, not by sources.
    matched_role: Optional[str] = None
    matched_pattern: Optional[str] = None
    seniority: Optional[str] = None
    is_remote: bool = False

    @property
    def fingerprint(self) -> str:
        """Stable identity for de-duplication: company + title.

        Not the URL: the same role appears on the company's ATS *and* on
        LinkedIn under different links, and it should only be reported once.

        Not the location either — that was the original design and it backfired.
        Sources spell the same office differently ("Tel Aviv, Israel" from
        LinkedIn, "Tel Aviv-Yafo, Tel Aviv District, Israel" from Greenhouse)
        and a single source rewords itself over time. Every respelling minted a
        fresh fingerprint, so the same job kept arriving as "new" — and it
        defeated the cross-source de-duplication above, because the two copies
        of one job never agreed on the location string.

        The cost: one company posting the same title in two Israeli cities is
        reported once rather than twice. That is far rarer than a respelling,
        and far less annoying than a repeat.
        """
        key = "|".join([_norm(self.company), _norm(self.title)])
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    def __str__(self) -> str:
        return f"{self.title} @ {self.company} ({self.location})"


@dataclass
class SourceResult:
    """Outcome of polling a single source — including honest failure info.

    Sources are expected to fail sometimes (rate limits, markup changes, bot
    walls). We never let one failure kill the run; we record it so `sources`
    can show you exactly what is and isn't working.
    """

    source: str
    jobs: List[Job] = field(default_factory=list)
    ok: bool = True
    error: Optional[str] = None
    duration_s: float = 0.0

    @property
    def status(self) -> str:
        return "ok" if self.ok else "FAILED"
