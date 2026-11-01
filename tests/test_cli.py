"""Exercise the CLI pipeline without network access or persistent scheduling."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internship_finder.__main__ import load_config, run, web_snapshot
from internship_finder.models import RawJob, SourceResult


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.config = self.base / "config.toml"
        self.write_config()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.close_log_handlers)

    @staticmethod
    def close_log_handlers():
        for handler in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(handler)
            handler.close()

    def write_config(self, scope="paris", zone="Europe/Paris", time="08:00"):
        self.config.write_text(f'''[search]
scope = "{scope}"
[schedule]
timezone = "{zone}"
time = "{time}"
[output]
report = "internship.md"
logs = "logs"
[[sources]]
name = "Fixture employer"
kind = "greenhouse"
board = "fixture"
''')

    def execute(self, sources, verifier=None, snapshot=None):
        # Every collector and verification response is injected; no test makes
        # live HTTP requests or relies on changing public vacancies.
        with contextlib.redirect_stderr(io.StringIO()):
            with patch("internship_finder.__main__.collect_sources", return_value=sources) as collector:
                with patch("internship_finder.verification.verify_job", side_effect=verifier) as verification:
                    code = run(self.config, snapshot)
        return code, collector, verification

    def status(self):
        return json.loads((self.base / "data" / "last-run.json").read_text())

    def test_successful_zero_matches_is_complete_and_updates_report(self):
        sources = [SourceResult("Employer", "https://employer.test", complete=True, pages=3)]
        code, collector, verification = self.execute(sources)
        self.assertEqual(code, 0)
        self.assertIn("No matching internships found", (self.base / "internship.md").read_text())
        self.assertEqual(self.status()["status"], "complete")
        self.assertEqual(self.status()["open"], 0)
        collector.assert_called_once()
        verification.assert_not_called()

    def test_partial_search_keeps_verified_matches_and_names_failed_source(self):
        job = RawJob(company="Example", title="Software engineering internship", location="Paris, France",
                     description="Implement C++ components.", contract_type="Internship", url="https://employer.test/jobs/123")
        def verified(candidate, client, now):
            candidate.status = "open"
            candidate.verified_at = now.isoformat()
            candidate.verification_evidence = "Employer application form available"
            return candidate
        sources = [SourceResult("Employer", "https://employer.test", jobs=[job], complete=True, pages=1),
                   SourceResult("Unavailable board", "https://blocked.test", complete=False, error="HTTP 503 after retries")]
        code, _, verification = self.execute(sources, verified)
        report = (self.base / "internship.md").read_text()
        self.assertEqual(code, 2)
        self.assertIn("Example", report)
        self.assertIn("INCOMPLETE", report)
        self.assertIn("Unavailable board", report)
        self.assertIn("HTTP 503 after retries", report)
        self.assertEqual(self.status()["open"], 1)
        self.assertEqual(self.status()["status"], "incomplete")
        verification.assert_called_once()

    def test_all_source_failure_preserves_previous_report_with_stale_banner(self):
        previous = b"# Previous results\nLast update: 2026-10-30T08:00:00+01:00\nA useful internship\n"
        path = self.base / "internship.md"
        path.write_bytes(previous)
        sources = [SourceResult("Unavailable employer", "https://employer.test", complete=False, error="Timeout after retries")]
        code, _, verification = self.execute(sources)
        report = path.read_bytes()
        self.assertEqual(code, 1)
        self.assertTrue(report.endswith(previous))
        self.assertIn(b"STALE", report)
        self.assertIn(b"Timeout after retries", report)
        self.assertEqual(self.status()["status"], "failed")
        verification.assert_not_called()

    def test_first_run_total_failure_does_not_claim_zero_results(self):
        code, _, _ = self.execute([SourceResult("Blocked", "https://blocked.test", complete=False, error="No connection")])
        self.assertEqual(code, 1)
        report = (self.base / "internship.md").read_text()
        self.assertIn("STALE", report)
        self.assertIn("No successful results are available yet", report)
        self.assertNotIn("No matching internships found", report)

    def test_unexpected_verifier_failure_retains_unverified_internship(self):
        job = RawJob(company="Example", title="Software internship", contract_type="Internship", location="Paris, France",
                     description="Develop C++ systems.", url="https://employer.test/jobs/123")
        def broken_verifier(*args):
            raise RuntimeError("Verification transport failed")
        code, _, _ = self.execute([SourceResult("Employer", "https://employer.test", jobs=[job], complete=True)], broken_verifier)
        self.assertEqual(code, 2)
        report = (self.base / "internship.md").read_text()
        open_section, unverified_section = report.split("## Unverified", 1)
        self.assertNotIn("### Example", open_section)
        self.assertIn("### Example", unverified_section)
        self.assertIn("Verification transport failed", unverified_section)
        self.assertEqual(self.status()["unverified"], 1)

    def test_invalid_timezone_is_rejected_before_collection(self):
        for zone in ["UTC", "Europe/London", "Not/A_Zone"]:
            with self.subTest(zone=zone):
                self.write_config(zone=zone)
                with self.assertRaisesRegex(ValueError, "Europe/Paris"):
                    load_config(self.config)

    def test_invalid_scope_and_daily_time_are_rejected(self):
        self.write_config(scope="france")
        with self.assertRaisesRegex(ValueError, "scope"):
            load_config(self.config)
        self.write_config(time="07:00")
        with self.assertRaisesRegex(ValueError, "08:00"):
            load_config(self.config)

    def test_old_or_naive_bootstrap_timestamp_is_rejected(self):
        now = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)
        archive = self.base / "archive.json"
        for timestamp in [(now - timedelta(days=2)).isoformat(), "2026-11-01T07:00:00"]:
            with self.subTest(timestamp=timestamp):
                archive.write_text(json.dumps({"captured_at": timestamp, "sources": []}))
                with self.assertRaisesRegex(ValueError, "24 hours"):
                    web_snapshot(archive, now)

    def test_fresh_bootstrap_is_one_time_and_never_complete(self):
        now = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)
        archive = self.base / "archive.json"
        for capture_field in ["captured_at", "collected_at"]:
            with self.subTest(capture_field=capture_field):
                archive.write_text(json.dumps({capture_field: now.isoformat(), "sources": [{"name": "Browser discovery", "complete": True,
                                          "jobs": [{"company": "Example", "title": "Intern", "status": "unverified"}]}]}))
                results = web_snapshot(archive, now)
                self.assertEqual(len(results), 1)
                self.assertFalse(results[0].complete)
                self.assertTrue(results[0].jobs[0].metadata["web_assisted"])
                self.assertEqual(results[0].jobs[0].verified_at, now.isoformat())


if __name__ == "__main__":
    unittest.main()
