"""Turn matched jobs into the WhatsApp digest text.

Kept plain-text and compact: CallMeBot sends the message in a URL, and WhatsApp
renders *bold* with single asterisks.
"""
from collections import OrderedDict
from datetime import datetime
from typing import List

from ..models import Job

ROLE_ICON = {
    "Data Engineer": "🛠",
    "Senior Data Analyst": "📊",
    "Junior Data Scientist": "🧪",
}


def build_digest(jobs: List[Job], max_jobs: int = 12) -> str:
    """Group by role, newest sources first, capped so the message stays sendable."""
    if not jobs:
        return ""

    total = len(jobs)
    shown = jobs[:max_jobs]

    grouped: "OrderedDict[str, List[Job]]" = OrderedDict()
    for job in shown:
        grouped.setdefault(job.matched_role or "Other", []).append(job)

    stamp = datetime.now().strftime("%d/%m %H:%M")
    header = "*%d new job%s* · %s" % (total, "" if total == 1 else "s", stamp)
    lines = [header, ""]

    for role, role_jobs in grouped.items():
        lines.append("%s *%s* (%d)" % (ROLE_ICON.get(role, "•"), role, len(role_jobs)))
        for job in role_jobs:
            tags = []
            if job.seniority:
                tags.append(job.seniority)
            if job.is_remote:
                tags.append("remote")
            tag_str = " [%s]" % ", ".join(tags) if tags else ""
            lines.append("• %s — %s%s" % (job.title, job.company, tag_str))
            lines.append("  %s · %s" % (job.location or "Israel", job.source))
            if job.url:
                lines.append("  %s" % job.url)
        lines.append("")

    if total > len(shown):
        lines.append("…and %d more. Run `jobtracker list` to see them all."
                     % (total - len(shown)))

    return "\n".join(lines).strip()


def build_summary(jobs: List[Job]) -> str:
    """One-line console summary."""
    if not jobs:
        return "No new matching jobs."
    by_role = {}
    for job in jobs:
        by_role[job.matched_role] = by_role.get(job.matched_role, 0) + 1
    parts = ", ".join("%s: %d" % (r, n) for r, n in sorted(by_role.items()))
    return "%d new job(s) — %s" % (len(jobs), parts)
