"""Israeli job boards: Drushim, AllJobs, JobMaster.

These are consumer sites with no public API, so they are scraped. Expect them to
be the most fragile part of the system — they are heavily JS-driven and change
their markup often. Each board is isolated: one breaking does not affect the
others, and `python -m jobtracker sources` shows you exactly which are alive.
"""
import json
import logging
import re
from typing import List

from bs4 import BeautifulSoup

from .. import http
from ..models import Job
from .base import Source

log = logging.getLogger(__name__)


class DrushimSource(Source):
    """Drushim exposes a JSON search API behind its SPA."""

    name = "drushim"
    API = "https://www.drushim.co.il/api/jobs/search/"

    def __init__(self, queries: List[str], max_pages: int = 2):
        self.queries = queries
        self.max_pages = max_pages

    def _search(self, term: str, page: int) -> List[Job]:
        session = http.get_session()
        payload = {
            "searchTerm": term,
            "page": page,
            "scopeIds": [],
            "professionValues": [],
            "regionValues": [],
        }
        resp = session.post(
            self.API, json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Referer": "https://www.drushim.co.il/"},
            timeout=http.DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("jobPage", {}).get("results") or data.get("results") or []
        jobs = []
        for j in results:
            job_id = j.get("jobId") or j.get("id") or ""
            jobs.append(Job(
                title=j.get("jobTitle") or j.get("title", ""),
                company=j.get("companyName") or j.get("company") or "Unknown",
                # Drushim is Israel-only, so a blank location is still Israel.
                location=", ".join(filter(None, [
                    j.get("cityName") or j.get("city") or "",
                    "Israel",
                ])),
                url="https://www.drushim.co.il/job/%s/" % job_id if job_id else "https://www.drushim.co.il/",
                source="drushim",
                description=(j.get("jobDescription") or j.get("description") or "")[:2000],
                posted_at=j.get("dateUpdated") or j.get("date"),
                external_id=str(job_id),
            ))
        return jobs

    def fetch(self) -> List[Job]:
        out, errors = [], []
        for term in self.queries:
            for page in range(1, self.max_pages + 1):
                try:
                    batch = self._search(term, page)
                except Exception as exc:  # noqa: BLE001
                    errors.append("%s p%d: %s" % (term, page, exc))
                    break
                if not batch:
                    break
                out.extend(batch)
        if errors and not out:
            raise RuntimeError("; ".join(errors[:2]))
        return out


class AllJobsSource(Source):
    """AllJobs — ASP.NET site, parsed from server-rendered HTML."""

    name = "alljobs"
    SEARCH = "https://www.alljobs.co.il/SearchResultsGuest.aspx"

    def __init__(self, queries: List[str]):
        self.queries = queries

    def _search(self, term: str) -> List[Job]:
        resp = http.get(self.SEARCH, params={
            "page": 1, "position": "", "type": "", "freetxt": term,
            "city": "", "region": "",
        }, headers={"Accept": "text/html,application/xhtml+xml"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        jobs = []
        for card in soup.select("div.job-content-top, div.JobItem"):
            link = card.select_one("a[href*='JobDetails'], h3 a, a.job-content-top-title")
            if not link:
                continue
            title = link.get_text(strip=True)
            if not title:
                continue
            company_el = card.select_one(".job-content-top-companyName, .CompanyName")
            loc_el = card.select_one(".job-content-top-location, .JobLocation")
            href = link.get("href", "")
            if href.startswith("/"):
                href = "https://www.alljobs.co.il" + href
            jobs.append(Job(
                title=title,
                company=company_el.get_text(strip=True) if company_el else "Unknown",
                location=(loc_el.get_text(strip=True) if loc_el else "") + ", Israel",
                url=href,
                source="alljobs",
            ))
        return jobs

    def fetch(self) -> List[Job]:
        out, errors = [], []
        for term in self.queries:
            try:
                out.extend(self._search(term))
            except Exception as exc:  # noqa: BLE001
                errors.append("%s: %s" % (term, exc))
        if errors and not out:
            raise RuntimeError("; ".join(errors[:2]))
        return out


class JobMasterSource(Source):
    """JobMaster — HTML search results."""

    name = "jobmaster"
    SEARCH = "https://www.jobmaster.co.il/jobs/"

    def __init__(self, queries: List[str]):
        self.queries = queries

    def _search(self, term: str) -> List[Job]:
        resp = http.get(self.SEARCH, params={"q": term},
                        headers={"Accept": "text/html,application/xhtml+xml"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        jobs = []
        # JobMaster ships JSON-LD JobPosting blocks — far more stable than
        # its CSS classes, so prefer them and fall back to markup.
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") != "JobPosting":
                    continue
                org = (item.get("hiringOrganization") or {}).get("name", "Unknown")
                loc = item.get("jobLocation") or {}
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
                jobs.append(Job(
                    title=item.get("title", ""),
                    company=org,
                    location=", ".join(filter(None, [
                        addr.get("addressLocality", ""), "Israel",
                    ])),
                    url=item.get("url", "") or self.SEARCH,
                    source="jobmaster",
                    description=re.sub(r"<[^>]+>", " ", item.get("description", ""))[:2000],
                    posted_at=item.get("datePosted"),
                ))
        return jobs

    def fetch(self) -> List[Job]:
        out, errors = [], []
        for term in self.queries:
            try:
                out.extend(self._search(term))
            except Exception as exc:  # noqa: BLE001
                errors.append("%s: %s" % (term, exc))
        if errors and not out:
            raise RuntimeError("; ".join(errors[:2]))
        return out
