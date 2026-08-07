"""Shared HTTP session: realistic headers, retries, and polite throttling.

Every outbound request in the app goes through here so that rate limiting and
backoff are enforced in one place rather than per-source.
"""
import logging
import random
import threading
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

try:  # urllib3 v2 and v1 put Retry in different places
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover
    from requests.packages.urllib3.util.retry import Retry  # type: ignore

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20

# Minimum seconds between requests to the same host. Scraped consumer sites get
# a much wider gap than public JSON APIs that are designed to be polled.
_HOST_DELAYS = {
    # Public ATS APIs are built to be polled; a token delay is enough to stay
    # polite without making a 25-company sweep take minutes.
    "boards-api.greenhouse.io": 0.05,
    "api.lever.co": 0.05,
    "api.ashbyhq.com": 0.05,
    "api.smartrecruiters.com": 0.05,
    "www.comeet.co": 0.2,
    "www.linkedin.com": 6.0,
    "linkedin.com": 6.0,
    "www.glassdoor.com": 8.0,
    "www.alljobs.co.il": 4.0,
    "www.drushim.co.il": 4.0,
    "www.jobmaster.co.il": 4.0,
    "www.facebook.com": 8.0,
}
_DEFAULT_DELAY = 0.4

_last_request = {}
_lock = threading.Lock()


def _throttle(host: str) -> None:
    """Block until enough time has passed since the last hit on this host."""
    delay = _HOST_DELAYS.get(host, _DEFAULT_DELAY)
    with _lock:
        last = _last_request.get(host, 0.0)
        wait = delay - (time.time() - last)
        if wait > 0:
            # Jitter so repeated runs don't form a detectable fixed cadence.
            time.sleep(wait + random.uniform(0, delay * 0.3))
        _last_request[host] = time.time()


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    })
    return session


_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = build_session()
    return _session


def get(url: str, **kwargs) -> requests.Response:
    """Throttled GET. Raises for HTTP errors so callers can report them."""
    from urllib.parse import urlparse

    _throttle(urlparse(url).netloc)
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    log.debug("GET %s", url)
    return get_session().get(url, **kwargs)


def get_json(url: str, **kwargs):
    kwargs.setdefault("headers", {}).setdefault("Accept", "application/json")
    resp = get(url, **kwargs)
    resp.raise_for_status()
    return resp.json()
