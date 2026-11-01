"""Human-readable reports, atomic replacement, and stale-result preservation."""
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import RawJob, SourceResult


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def clean(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", str(value)).replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("*", "\\*").replace("[", "\\[").strip() or "Not provided"


def link(url: str) -> str:
    from urllib.parse import urlsplit
    if urlsplit(url).scheme not in {"https", "http"}:
        return "Not provided"
    return "[Apply](<" + url.replace(">", "%3E").replace("<", "%3C").replace("\n", "") + ">)"


def _entry(job: RawJob) -> str:
    values = [f"### {clean(job.company)} — {clean(job.title)}", "",
              f"- **Location / work arrangement:** {clean(job.location)} / {clean(job.work_arrangement)}",
              f"- **C++ evidence:** {clean(job.metadata.get('cpp_evidence', ''))}",
              f"- **Start / duration:** {clean(job.start_date)} / {clean(job.duration)}",
              f"- **Posting date:** {clean(job.posted_at)}",
              f"- **Application:** {link(job.apply_url or job.url)}",
              f"- **Listing:** {link(job.url).replace('[Apply]', '[Source]')}",
              f"- **Last verification attempt:** {clean(job.verified_at)}",
              f"- **Availability:** {clean(job.status)} — {clean(job.verification_evidence)}",
              f"- **Discovered on:** {clean(', '.join(job.metadata.get('discovered_on', [job.source])))}", ""]
    return "\n".join(values)


def render_report(jobs: list[RawJob], sources: list[SourceResult], now: datetime, scope: str,
                  limitations: list[str], verification_incomplete: bool = False) -> str:
    local = now.astimezone(ZoneInfo("Europe/Paris"))
    incomplete = any(not s.complete for s in sources) or verification_incomplete
    opened = [j for j in jobs if j.status == "open"]
    unverified = [j for j in jobs if j.status == "unverified"]
    lines = ["# C++ internships — Paris", "", f"Last update: **{local.isoformat(timespec='seconds')} (Europe/Paris)**",
             f"Search status: **{'INCOMPLETE — partial results' if incomplete else 'COMPLETE for configured sources'}**",
             f"Location scope: **{scope}**", "", f"Confirmed accepting applications: **{len(opened)}** · Unverified: **{len(unverified)}**", "", "## Open internships", ""]
    if opened:
        lines.extend(_entry(j) for j in opened)
    elif incomplete:
        lines.append("No open internships were confirmed. The search was incomplete; this does not establish that no matching internships exist.\n")
    else:
        lines.append("No matching internships found in the successfully searched configured sources.\n")
    lines.extend(["## Unverified", "", "These match the role/location filters, but accepting applications could not be confirmed.", ""])
    lines.extend(_entry(j) for j in unverified)
    if not unverified:
        lines.append("None.\n")
    lines.extend(["## Sources searched", "", "| Source | Pages recorded | Listings collected | Result |", "|---|---:|---:|---|"])
    for source in sources:
        status = "Completed" if source.complete else "INCOMPLETE: " + (source.error or "Collection not confirmed complete")
        lines.append(f"| {clean(source.name)} | {source.pages} | {len(source.jobs)} | {clean(status)} |")
    lines.extend(["", "## Coverage limitations", ""])
    lines.extend("- " + clean(x) for x in limitations)
    lines.append("- Coverage is limited to configured public sources and discoverable pages. No claim is made that every vacancy was found. Closed listings are excluded. Missing fields are shown as ‘Not provided’.\n")
    return "\n".join(lines)


def preserve_stale(path: Path, now: datetime, reason: str) -> None:
    previous = path.read_text(encoding="utf-8") if path.exists() else "# C++ internships — Paris\n\nNo successful results are available yet. Collection failed; this is not a zero-match result.\n"
    previous = re.sub(r"\A<!-- stale -->.*?<!-- /stale -->\n\n", "", previous, flags=re.S)
    timestamp = now.astimezone(ZoneInfo("Europe/Paris")).isoformat(timespec="seconds")
    banner = f"<!-- stale -->\n> **STALE — collection failed entirely** at {timestamp} (Europe/Paris).\n> {clean(reason)}\n> Previous results and their original update/verification timestamps are preserved below; their availability may have changed.\n<!-- /stale -->\n\n"
    atomic_write(path, banner + previous)
