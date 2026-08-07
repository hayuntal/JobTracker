"""Source plugin contract.

Every source returns a SourceResult rather than raising, so one broken scraper
can never take down the whole run. `available()` lets a source declare itself
unusable (missing credentials, disabled in config) without being treated as a
failure.
"""
import logging
import time
from typing import List

from ..models import Job, SourceResult

log = logging.getLogger(__name__)


class Source:
    #: short identifier used in logs, config and the `sources` report
    name = "base"

    #: set False for sources known to be brittle/gray-area; they must then be
    #: switched on explicitly via config or CLI flag.
    enabled_by_default = True

    def available(self) -> bool:
        """Whether this source can run at all (e.g. has required credentials)."""
        return True

    def unavailable_reason(self) -> str:
        return "not configured"

    def fetch(self) -> List[Job]:  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def run(self) -> SourceResult:
        started = time.time()
        if not self.available():
            return SourceResult(
                source=self.name, jobs=[], ok=False,
                error=self.unavailable_reason(), duration_s=0.0,
            )
        try:
            jobs = self.fetch()
            return SourceResult(
                source=self.name, jobs=jobs, ok=True,
                duration_s=time.time() - started,
            )
        except Exception as exc:  # noqa: BLE001 - deliberate catch-all
            log.warning("Source %s failed: %s", self.name, exc)
            return SourceResult(
                source=self.name, jobs=[], ok=False,
                error="%s: %s" % (type(exc).__name__, exc),
                duration_s=time.time() - started,
            )
