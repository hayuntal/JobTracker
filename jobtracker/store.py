"""Persistent record of jobs already sent, so you're never alerted twice.

Stored as pretty-printed JSON rather than SQLite on purpose: the GitHub Actions
workflow commits this file back to the repo after each run, and JSON produces
readable diffs while SQLite would produce an opaque binary blob.
"""
import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List

from .models import Job

log = logging.getLogger(__name__)

# Forget postings after this long so a re-listed role can alert again, and the
# state file doesn't grow forever.
RETENTION_DAYS = 120


class SeenStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: Dict[str, Dict] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = raw.get("seen", raw) if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt state file must not stop the run — worst case you get
            # one duplicate digest, which is far better than no alerts at all.
            log.warning("Could not read state file %s (%s); starting fresh", self.path, exc)
            self._data = {}

    def has(self, job: Job) -> bool:
        return job.fingerprint in self._data

    def new_jobs(self, jobs: Iterable[Job]) -> List[Job]:
        """Filter to jobs not seen before, de-duplicated within this batch too."""
        out, batch = [], set()
        for job in jobs:
            fp = job.fingerprint
            if fp in self._data or fp in batch:
                continue
            batch.add(fp)
            out.append(job)
        return out

    def mark_seen(self, jobs: Iterable[Job]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for job in jobs:
            self._data[job.fingerprint] = {
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "source": job.source,
                "url": job.url,
                "first_seen": now,
            }

    def merge(self, other: Dict[str, Dict]) -> int:
        """Fold another store's records into this one. Returns records added.

        A union, not last-writer-wins: if two runs each alerted on different
        jobs, discarding either side's records means those jobs get sent again.
        Being order-independent is what lets CI retry safely — it can re-apply
        this on top of whatever landed on the remote meanwhile, as many times
        as needed, and converge on the same answer.
        """
        added = 0
        for fingerprint, record in (other or {}).items():
            existing = self._data.get(fingerprint)
            if existing is None:
                self._data[fingerprint] = record
                added += 1
                continue
            # Same job recorded twice: keep the earlier sighting so retention
            # pruning measures from when it was really first seen.
            theirs = record.get("first_seen") or ""
            ours = existing.get("first_seen") or ""
            if theirs and (not ours or theirs < ours):
                existing["first_seen"] = theirs
        return added

    def merge_file(self, path: Path) -> int:
        """Merge the `seen` map of another state file on disk."""
        path = Path(path)
        if not path.exists():
            log.warning("Nothing to merge: %s does not exist", path)
            return 0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read %s (%s); nothing merged", path, exc)
            return 0
        data = raw.get("seen", raw) if isinstance(raw, dict) else {}
        return self.merge(data if isinstance(data, dict) else {})

    def prune(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        removed = []
        for fp, rec in self._data.items():
            try:
                seen = datetime.fromisoformat(rec.get("first_seen", ""))
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
                if seen < cutoff:
                    removed.append(fp)
            except (ValueError, TypeError):
                continue
        for fp in removed:
            del self._data[fp]
        return len(removed)

    def save(self) -> None:
        """Atomic write — a run killed mid-save must not corrupt the state."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(self._data),
            "seen": self._data,
        }
        tmp = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent),
            prefix=self.path.name, suffix=".tmp", delete=False,
        )
        try:
            json.dump(payload, tmp, ensure_ascii=False, indent=2, sort_keys=True)
            tmp.flush()
            tmp.close()
            Path(tmp.name).replace(self.path)
        except BaseException:
            Path(tmp.name).unlink(missing_ok=True)
            raise

    def __len__(self) -> int:
        return len(self._data)
