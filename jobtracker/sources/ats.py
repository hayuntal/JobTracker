"""Company careers pages, read through their ATS APIs.

This is the backbone of the tracker: it is literally the same data the company's
own careers page renders, served as structured JSON. No login, no scraping, no
markup to break.

Each provider gets a small adapter that maps its payload onto our Job model.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from typing import Any, Callable, Dict, List

from .. import http
from ..config import Company
from ..models import Job, SourceResult
from .base import Source

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _text(html: str, limit: int = 4000) -> str:
    """Strip HTML down to plain text for keyword/location checks."""
    if not html:
        return ""
    plain = _TAG_RE.sub(" ", unescape(html))
    return re.sub(r"\s+", " ", plain).strip()[:limit]


# --------------------------------------------------------------------------
# Provider adapters: each returns a list of Job for one company.
# --------------------------------------------------------------------------

def _greenhouse(c: Company) -> List[Job]:
    # Deliberately WITHOUT `content=true`. Pulling full descriptions for every
    # posting turned a 25-company sweep into a 10-minute job; the pipeline
    # back-fills descriptions later for the handful that actually need one.
    url = "https://boards-api.greenhouse.io/v1/boards/%s/jobs" % c.token
    data = http.get_json(url)
    jobs = []
    for j in data.get("jobs", []):
        job_id = str(j.get("id", ""))
        jobs.append(Job(
            title=j.get("title", ""),
            company=c.name,
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            source="greenhouse",
            description="",
            posted_at=j.get("updated_at") or j.get("first_published"),
            external_id=job_id,
            detail_url=("https://boards-api.greenhouse.io/v1/boards/%s/jobs/%s"
                        % (c.token, job_id)) if job_id else None,
        ))
    return jobs


def _lever(c: Company) -> List[Job]:
    url = "https://api.lever.co/v0/postings/%s?mode=json" % c.token
    data = http.get_json(url)
    jobs = []
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        jobs.append(Job(
            title=j.get("text", ""),
            company=c.name,
            location=cats.get("location", ""),
            url=j.get("hostedUrl", ""),
            source="lever",
            description=_text(j.get("descriptionPlain") or j.get("description", "")),
            posted_at=str(j.get("createdAt", "")) or None,
            external_id=str(j.get("id", "")),
        ))
    return jobs


def _ashby(c: Company) -> List[Job]:
    url = "https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=false" % c.token
    data = http.get_json(url)
    jobs = []
    for j in data.get("jobs", []):
        jobs.append(Job(
            title=j.get("title", ""),
            company=c.name,
            location=j.get("location", "") or (j.get("address") or {}).get("postalAddress", {}).get("addressRegion", ""),
            url=j.get("jobUrl", "") or j.get("applyUrl", ""),
            source="ashby",
            description=_text(j.get("descriptionPlain") or j.get("descriptionHtml", "")),
            posted_at=j.get("publishedAt"),
            external_id=str(j.get("id", "")),
        ))
    return jobs


def _smartrecruiters(c: Company) -> List[Job]:
    """SmartRecruiters paginates; walk until we've seen everything."""
    jobs, offset = [], 0
    while True:
        url = ("https://api.smartrecruiters.com/v1/companies/%s/postings"
               "?limit=100&offset=%d" % (c.token, offset))
        data = http.get_json(url)
        content = data.get("content", [])
        if not content:
            break
        for j in content:
            loc = j.get("location") or {}
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            jobs.append(Job(
                title=j.get("name", ""),
                company=c.name,
                location=", ".join(p for p in parts if p),
                url=(j.get("applyUrl")
                     or "https://jobs.smartrecruiters.com/%s/%s" % (c.token, j.get("id", ""))),
                source="smartrecruiters",
                description="",
                posted_at=j.get("releasedDate"),
                external_id=str(j.get("id", "")),
            ))
        offset += len(content)
        if offset >= data.get("totalFound", 0) or len(content) < 100:
            break
    return jobs


def _comeet(c: Company) -> List[Job]:
    """Comeet — dominant among Israeli companies.

    Needs a uid + token pair that only appears in the rendered careers page;
    `/add-company` extracts it with a browser.
    """
    if not (c.uid and c.token):
        raise ValueError("comeet requires both 'uid' and 'token' in companies.yaml")
    url = ("https://www.comeet.co/careers-api/2.0/company/%s/positions?token=%s"
           % (c.uid, c.token))
    data = http.get_json(url)
    jobs = []
    for j in data if isinstance(data, list) else data.get("positions", []):
        loc = j.get("location") or {}
        parts = [loc.get("city"), loc.get("country")]
        jobs.append(Job(
            title=j.get("name", ""),
            company=c.name,
            location=", ".join(p for p in parts if p) or loc.get("name", ""),
            url=j.get("url_active_page") or j.get("url_comeet_hosted_page", ""),
            source="comeet",
            description=_text(" ".join(
                str(d.get("value", "")) for d in (j.get("details") or [])
            )),
            posted_at=j.get("time_updated"),
            external_id=str(j.get("uid", "")),
        ))
    return jobs


def _workday(c: Company) -> List[Job]:
    """Workday CXS endpoint. Tenant in `token`, career site name in `site`.

    The `wdN` host number is per-tenant (not always `wd3`) and isn't guessable
    from the tenant name, so it's read from `wd` in companies.yaml (default 3).
    """
    if not (c.token and c.site):
        raise ValueError("workday requires 'token' (tenant) and 'site' in companies.yaml")
    wd = str((c.extra or {}).get("wd", "3"))
    host = "%s.wd%s.myworkdayjobs.com" % (c.token, wd)
    base = "https://%s/wday/cxs/%s/%s/jobs" % (host, c.token, c.site)
    session = http.get_session()
    jobs, offset = [], 0
    while offset < 500:
        resp = session.post(
            base, json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""},
            timeout=http.DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for j in postings:
            path = j.get("externalPath", "")
            jobs.append(Job(
                title=j.get("title", ""),
                company=c.name,
                location=j.get("locationsText", ""),
                url="https://%s/%s%s" % (host, c.site, path),
                source="workday",
                description="",
                posted_at=j.get("postedOn"),
                external_id=j.get("bulletFields", [""])[0] if j.get("bulletFields") else path,
            ))
        offset += len(postings)
        if offset >= data.get("total", 0):
            break
    return jobs


PROVIDERS: Dict[str, Callable[[Company], List[Job]]] = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
    "comeet": _comeet,
    "workday": _workday,
}


def enrich_descriptions(jobs: List[Job], max_workers: int = 8) -> int:
    """Back-fill descriptions for jobs that carry a detail_url.

    Called only for postings the title matcher already accepted *and* whose
    location was too vague to decide on — typically a bare "Remote". Keeps the
    expensive per-job fetch proportional to matches, not to total listings.
    """
    targets = [j for j in jobs if j.detail_url and not j.description]
    if not targets:
        return 0

    def _one(job: Job) -> None:
        data = http.get_json(job.detail_url)
        job.description = _text(data.get("content", ""))

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_one, j): j for j in targets}
        for future in as_completed(futures):
            try:
                future.result()
                done += 1
            except Exception as exc:  # noqa: BLE001
                # No description just means we fall back to the location text.
                log.debug("Detail fetch failed for %s: %s", futures[future].title, exc)
    return done


class ATSSource(Source):
    """Polls every configured company's ATS, in parallel."""

    name = "careers"

    def __init__(self, companies: List[Company], max_workers: int = 8):
        self.companies = companies
        self.max_workers = max_workers
        #: per-company outcome, surfaced by the `sources` command
        self.company_results: Dict[str, Dict[str, Any]] = {}

    def _one(self, company: Company) -> List[Job]:
        fn = PROVIDERS.get(company.provider)
        if fn is None:
            raise ValueError("unknown provider '%s'" % company.provider)
        return fn(company)

    def fetch(self) -> List[Job]:
        all_jobs: List[Job] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._one, c): c for c in self.companies}
            for future in as_completed(futures):
                company = futures[future]
                try:
                    jobs = future.result()
                    all_jobs.extend(jobs)
                    self.company_results[company.name] = {"ok": True, "jobs": len(jobs)}
                except Exception as exc:  # noqa: BLE001
                    # One dead company must never sink the run.
                    log.warning("Careers fetch failed for %s: %s", company.name, exc)
                    self.company_results[company.name] = {
                        "ok": False, "jobs": 0,
                        "error": "%s: %s" % (type(exc).__name__, exc),
                    }
        return all_jobs

    def run(self) -> SourceResult:
        result = super().run()
        failed = [n for n, r in self.company_results.items() if not r["ok"]]
        if failed and result.ok:
            result.error = "%d/%d companies failed: %s" % (
                len(failed), len(self.companies), ", ".join(sorted(failed)[:5]),
            )
        return result
