"""Glassdoor and Facebook groups.

Read this before you rely on either:

Glassdoor sits behind Cloudflare bot protection. Plain HTTP requests are usually
answered with an interstitial challenge rather than job listings. The adapter
below tries the JSON-LD path that sometimes survives, and reports honestly when
it is blocked instead of silently returning nothing.

Facebook groups are not publicly readable — job posts live in groups that
require a logged-in account, and the content is unstructured free text with no
consistent title/location fields. This adapter is therefore OFF unless you set
FACEBOOK_COOKIE, and even then treat it as best-effort.

Both are disabled by default. Enable with `--enable glassdoor,facebook`.
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

_BLOCK_SIGNS = re.compile(
    r"just a moment|cf-browser-verification|captcha|access denied|"
    r"unusual traffic|enable javascript and cookies",
    re.IGNORECASE,
)


class GlassdoorSource(Source):
    name = "glassdoor"
    enabled_by_default = False

    SEARCH = "https://www.glassdoor.com/Job/israel-{slug}-jobs-SRCH_IL.0,6_IN119_KO7,{end}.htm"

    def __init__(self, queries: List[str]):
        self.queries = queries

    def _search(self, term: str) -> List[Job]:
        slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
        url = self.SEARCH.format(slug=slug, end=7 + len(slug))
        resp = http.get(url, headers={
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "https://www.glassdoor.com/",
        })
        if resp.status_code in (403, 429) or _BLOCK_SIGNS.search(resp.text[:5000]):
            raise RuntimeError(
                "blocked by Glassdoor bot protection (HTTP %d) — expected; "
                "this source is unreliable by design" % resp.status_code
            )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        jobs = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            for item in (data if isinstance(data, list) else [data]):
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
                        addr.get("addressLocality", ""),
                        addr.get("addressCountry", "") if isinstance(addr.get("addressCountry"), str) else "",
                    ])) or "Israel",
                    url=item.get("url", url),
                    source="glassdoor",
                    posted_at=item.get("datePosted"),
                ))
        return jobs

    def fetch(self) -> List[Job]:
        out, errors = [], []
        for term in self.queries:
            try:
                out.extend(self._search(term))
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        if errors and not out:
            raise RuntimeError(errors[0])
        return out


class FacebookGroupsSource(Source):
    """Israeli tech-jobs Facebook groups. Requires a logged-in session cookie."""

    name = "facebook"
    enabled_by_default = False

    # Well-known Israeli hi-tech job groups.
    GROUPS = [
        "1442133162677776",   # Jobs in Hi-Tech Israel
        "230376130430757",    # Data Science Israel jobs
    ]

    def __init__(self, cookie: str, queries: List[str]):
        self.cookie = cookie
        self.queries = queries

    def available(self) -> bool:
        return bool(self.cookie)

    def unavailable_reason(self) -> str:
        return ("no FACEBOOK_COOKIE set — Facebook groups need a logged-in "
                "session; source skipped")

    def fetch(self) -> List[Job]:
        jobs: List[Job] = []
        errors = []
        for group_id in self.GROUPS:
            url = "https://www.facebook.com/groups/%s" % group_id
            try:
                resp = http.get(url, headers={
                    "Cookie": self.cookie,
                    "Accept": "text/html,application/xhtml+xml",
                })
                if _BLOCK_SIGNS.search(resp.text[:5000]) or "login" in resp.url:
                    raise RuntimeError("session cookie rejected or expired")
                resp.raise_for_status()
                jobs.extend(self._parse_posts(resp.text, url))
            except Exception as exc:  # noqa: BLE001
                errors.append("group %s: %s" % (group_id, exc))
        if errors and not jobs:
            raise RuntimeError("; ".join(errors[:2]))
        return jobs

    def _parse_posts(self, html: str, group_url: str) -> List[Job]:
        """Extract candidate roles from free-text posts.

        Group posts have no structure, so this pulls plausible job-title lines
        out of the text. It is inherently lossy — the matcher does the real
        filtering downstream, and anything ambiguous simply won't match.
        """
        soup = BeautifulSoup(html, "lxml")
        text_blocks = [el.get_text(" ", strip=True) for el in soup.select("div[dir='auto'], p")]
        jobs = []
        seen = set()
        for block in text_blocks:
            if len(block) < 25 or len(block) > 1200:
                continue
            # Take the first line as a title candidate; job posts almost always
            # lead with the role.
            first_line = block.split("\n")[0][:120].strip()
            if not first_line or first_line in seen:
                continue
            seen.add(first_line)
            jobs.append(Job(
                title=first_line,
                company="(Facebook group post)",
                location="Israel",
                url=group_url,
                source="facebook",
                description=block[:1500],
            ))
        return jobs
