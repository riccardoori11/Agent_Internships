"""Entrypoint: python3 -m internship_finder --config config.toml."""
import argparse
import fcntl
import json
import logging
import logging.handlers
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .http import HttpClient
from .matching import deduplicate, match_job
from .models import RawJob, SourceResult
from .report import atomic_write, preserve_stale, render_report
from .sources import collect_sources

LOG = logging.getLogger("internship_finder")


def load_config(path: Path) -> dict:
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    search = config.setdefault("search", {})
    if search.get("scope", "paris") not in {"paris", "ile-de-france"}:
        raise ValueError("search.scope must be paris or ile-de-france")
    if config.get("schedule", {}).get("timezone", "Europe/Paris") != "Europe/Paris":
        raise ValueError("schedule.timezone must be Europe/Paris")
    if config.get("schedule", {}).get("time", "08:00") != "08:00":
        raise ValueError("schedule.time must be 08:00")
    ZoneInfo("Europe/Paris")  # Fail usefully if tzdata is missing.
    if not config.get("sources"):
        raise ValueError("Configure at least one [[sources]] entry")
    network = config.get("network", {})
    if not 0 <= network.get("retries", 3) <= 10 or network.get("timeout", 20) <= 0:
        raise ValueError("network retries must be 0..10 and timeout must be positive")
    return config


def web_snapshot(path: Path, now: datetime) -> list[SourceResult]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # A manually fetched archive is an explicit one-time input, never a daily
    # network fallback. Refuse stale imports rather than renewing their freshness.
    captured_text = data.get("captured_at") or data["collected_at"]
    captured = datetime.fromisoformat(captured_text)
    if captured.tzinfo is None or abs((now - captured).total_seconds()) > 86400:
        raise ValueError("Web-assisted input must have a timezone-aware capture within the last 24 hours")
    results = []
    for source in data["sources"]:
        jobs = [RawJob(**job) for job in source.get("jobs", [])]
        for job in jobs:
            if not job.verified_at:
                job.verified_at = captured_text
            job.metadata["web_assisted"] = True
        results.append(SourceResult(name=source["name"], url=source.get("url", ""), jobs=jobs,
                                    pages=source.get("pages", 0), complete=False,
                                    error=source.get("error") or "Web-assisted discovery; indexed results/pagination could not be exhausted"))
    return results


def run(config_path: Path, snapshot: Path | None = None) -> int:
    config_path = config_path.resolve()
    config = load_config(config_path)
    base = config_path.parent
    report = base / config.get("output", {}).get("report", "internship.md")
    log_dir = base / config.get("output", {}).get("logs", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_dir / "internship-finder.log", maxBytes=2_000_000, backupCount=5)
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s", handlers=[handler, logging.StreamHandler()], force=True)
    logging.Formatter.converter = __import__("time").gmtime
    lock = (base / ".internship-finder.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        LOG.info("Another collection holds the lock; skipping")
        lock.close()
        return 3
    now = datetime.now(timezone.utc)
    status_path = base / "data" / "last-run.json"
    scope = config.get("search", {}).get("scope", "paris")
    try:
        settings = config.get("network", {})
        client = HttpClient(timeout=settings.get("timeout", 20), retries=settings.get("retries", 3))
        LOG.info("Starting real collection scope=%s web_assisted=%s", scope, bool(snapshot))
        sources = collect_sources(config, client)
        if snapshot:
            sources.extend(web_snapshot(snapshot, now))
        collected = [job for source in sources for job in source.jobs]
        # Empty, incomplete sources cannot establish a successful zero-match search.
        if not collected and not any(s.complete for s in sources):
            raise RuntimeError("All configured sources failed: " + "; ".join(s.name + ": " + s.error for s in sources))
        candidates = []
        excluded = 0
        for job in collected:
            match, reason = match_job(job, scope, config.get("search", {}).get("idf_locations", []))
            if match:
                candidates.append(job)
            else:
                excluded += 1
                LOG.debug("Exclude %s: %s", job.url, reason)
        # Verify duplicates before merging: an inaccessible original must not hide
        # an employer-confirmed mirror's status without an audit trail.
        from .verification import verify_job

        def verify(job):
            if job.metadata.get("web_assisted"):
                return job  # Timestamp/evidence are the actual browser attempt.
            try:
                return verify_job(job, client, now)
            except Exception as exc:
                LOG.exception("Verification failed %s", job.url)
                job.status = "unverified"
                job.verified_at = now.isoformat(timespec="seconds")
                job.verification_evidence = f"Verification failed: {exc}"
                return job

        with ThreadPoolExecutor(max_workers=settings.get("verification_workers", 4)) as pool:
            candidates = list(pool.map(verify, candidates))
        jobs = deduplicate(candidates)
        unknown = any(job.status == "unverified" for job in jobs)
        limitations = list(config.get("search", {}).get("limitations", []))
        if snapshot:
            limitations.append("This initial run includes an explicitly imported web-assisted search archive. Direct collector network access was blocked. Daily scheduled runs always use live HTTP and never reuse this archive.")
        incomplete = unknown or any(not s.complete for s in sources)
        output = render_report(jobs, sources, datetime.now(timezone.utc), scope, limitations, unknown)
        atomic_write(report, output)
        status = {"attempted_at": now.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
                  "status": "incomplete" if incomplete else "complete", "report": str(report),
                  "collected": len(collected), "excluded": excluded, "matches": len(jobs),
                  "open": sum(j.status == "open" for j in jobs), "unverified": sum(j.status == "unverified" for j in jobs),
                  "sources": [{"name": s.name, "complete": s.complete, "pages": s.pages, "error": s.error} for s in sources]}
        atomic_write(status_path, json.dumps(status, indent=2) + "\n")
        LOG.info("Report saved %s status=%s open=%s unverified=%s", report, status["status"], status["open"], status["unverified"])
        return 2 if incomplete else 0
    except Exception as exc:
        LOG.exception("Collection failed; preserving previous results")
        preserve_stale(report, datetime.now(timezone.utc), str(exc))
        atomic_write(status_path, json.dumps({"attempted_at": now.isoformat(), "status": "failed", "error": str(exc), "report": str(report)}, indent=2) + "\n")
        return 1
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--web-snapshot", type=Path, help="Explicit fresh one-time browser archive; never used by schedule")
    args = parser.parse_args()
    try:
        return run(args.config, args.web_snapshot)
    except (ValueError, OSError, KeyError) as exc:
        print(f"Configuration/startup failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
