"""Turn matched jobs into the alert text.

Two renderings of the same digest:

    telegram  HTML — the job title becomes the link, which is both shorter and
                     nicer to tap than a bare URL on its own line.
    plain     asterisk-bold — readable in a terminal, and what WhatsApp renders.

Everything the source gave us is untrusted text, so the HTML rendering escapes
it. An unescaped '&' or '<' in a job title makes Telegram reject the whole
message with a parse error — which would look exactly like "no jobs today".
"""
import html
from collections import OrderedDict
from datetime import datetime
from typing import List

from ..models import Job

ROLE_ICON = {
    "Data Engineer": "🛠",
    "Senior Data Analyst": "📊",
    "Junior Data Scientist": "🧪",
}


def _esc(text: str, style: str) -> str:
    return html.escape(text or "") if style == "telegram" else (text or "")


def _bold(text: str, style: str) -> str:
    return "<b>%s</b>" % text if style == "telegram" else "*%s*" % text


def _italic(text: str, style: str) -> str:
    return "<i>%s</i>" % text if style == "telegram" else "[%s]" % text


def build_digest(jobs: List[Job], remaining: int = 0, style: str = "plain") -> str:
    """Render exactly the jobs passed in, grouped by role.

    `jobs` is already capped by the caller — this function never drops any, so
    what it renders is exactly what gets marked as seen. `remaining` is how many
    more are waiting for the next run, and is only mentioned in a footer.
    """
    if not jobs:
        return ""

    total = len(jobs)
    grouped: "OrderedDict[str, List[Job]]" = OrderedDict()
    for job in jobs:
        grouped.setdefault(job.matched_role or "Other", []).append(job)

    stamp = datetime.now().strftime("%d/%m %H:%M")
    lines = ["%s · %s" % (_bold("%d new job%s" % (total, "" if total == 1 else "s"), style),
                          stamp), ""]

    for role, role_jobs in grouped.items():
        lines.append("%s %s (%d)" % (ROLE_ICON.get(role, "•"),
                                     _bold(_esc(role, style), style), len(role_jobs)))
        for job in role_jobs:
            title = _esc(job.title, style)
            if job.url and style == "telegram":
                title = '<a href="%s">%s</a>' % (html.escape(job.url, quote=True), title)

            tags = []
            if job.seniority:
                tags.append(job.seniority)
            if job.is_remote:
                tags.append("remote")
            tag_str = " " + _italic(", ".join(tags), style) if tags else ""

            lines.append("• %s — %s%s" % (title, _esc(job.company, style), tag_str))
            lines.append("  %s · %s" % (_esc(job.location or "Israel", style),
                                        _esc(job.source, style)))
            if job.url and style != "telegram":
                lines.append("  %s" % job.url)
        lines.append("")

    if remaining > 0:
        lines.append("…and %d more queued — they arrive in the next run." % remaining)

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
