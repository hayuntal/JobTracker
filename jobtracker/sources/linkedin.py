"""LinkedIn, via the public guest job-search endpoint (no login).

This is the same endpoint that powers the job list an anonymous visitor sees, so
it needs no account and no cookies. It is still LinkedIn: it rate-limits hard,
returns 429 when annoyed, and the markup can change without warning. Requests
are throttled to one per ~6s in http.py and the source fails soft.
"""
import logging
from typing import List
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .. import http
from ..models import Job
from .base import Source

log = logging.getLogger(__name__)

ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# LinkedIn's numeric geo id for Israel. More reliable than the free-text
# location parameter, which silently matches other countries.
GEO_ID_ISRAEL = "101620260"


class LinkedInSource(Source):
    name = "linkedin"

    def __init__(self, queries: List[str], pages_per_query: int = 2,
                 posted_within_seconds: int = 86400 * 7):
        self.queries = queries
        self.pages_per_query = pages_per_query
        self.posted_within_seconds = posted_within_seconds

    def _search(self, keywords: str, start: int) -> List[Job]:
        params = {
            "keywords": keywords,
            "geoId": GEO_ID_ISRAEL,
            "location": "Israel",
            "f_TPR": "r%d" % self.posted_within_seconds,
            "start": start,
        }
        url = "%s?%s" % (ENDPOINT, urlencode(params))
        resp = http.get(url, headers={
            "Accept": "text/html,application/xhtml+xml",
            "X-Requested-With": "XMLHttpRequest",
        })
        if resp.status_code == 429:
            raise RuntimeError("rate limited by LinkedIn (429) — backing off")
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        jobs = []
        for card in soup.select("li"):
            title_el = card.select_one(".base-search-card__title")
            company_el = card.select_one(".base-search-card__subtitle")
            loc_el = card.select_one(".job-search-card__location")
            link_el = card.select_one("a.base-card__full-link") or card.select_one("a[href]")
            time_el = card.select_one("time")
            if not (title_el and link_el):
                continue
            jobs.append(Job(
                title=title_el.get_text(strip=True),
                company=company_el.get_text(strip=True) if company_el else "Unknown",
                location=loc_el.get_text(strip=True) if loc_el else "Israel",
                # Strip the tracking query string; it changes every request and
                # would defeat de-duplication if it ever reached a fingerprint.
                url=link_el.get("href", "").split("?")[0],
                source="linkedin",
                posted_at=time_el.get("datetime") if time_el else None,
            ))
        return jobs

    def fetch(self) -> List[Job]:
        out, errors = [], []
        for query in self.queries:
            for page in range(self.pages_per_query):
                try:
                    batch = self._search(query, page * 25)
                except Exception as exc:  # noqa: BLE001
                    errors.append("%s p%d: %s" % (query, page, exc))
                    break  # move to the next query rather than hammering
                if not batch:
                    break
                out.extend(batch)
        if errors and not out:
            raise RuntimeError("; ".join(errors[:3]))
        if errors:
            log.warning("LinkedIn partial failures: %s", "; ".join(errors[:3]))
        return out
