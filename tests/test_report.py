from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internship_finder.models import RawJob, SourceResult
from internship_finder.report import atomic_write, preserve_stale, render_report


NOW = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)


class ReportTests(unittest.TestCase):
    def render(self, jobs=(), sources=None, **kwargs):
        if sources is None:
            sources = [SourceResult("Employer", "https://employer.test", complete=True, pages=1)]
        return render_report(list(jobs), sources, NOW, "paris", ["Public pages only."], **kwargs)

    def test_required_fields_timezone_and_unknown_values(self):
        job = RawJob(company="Example", title="C++ Intern", location="Paris, France", work_arrangement="Hybrid",
                     url="https://example.test/job", apply_url="https://example.test/apply", start_date="February 2027", duration="6 months",
                     posted_at="2026-10-30", verified_at="2026-11-01T08:00:00+01:00", status="open",
                     metadata={"cpp_evidence": "Implement C++ components."})
        report = self.render([job])
        for value in ["Example", "C++ Intern", "Paris, France", "Hybrid", "Implement C++ components.",
                      "February 2027", "6 months", "2026-10-30", "https://example.test/apply",
                      "2026-11-01T08:00:00+01:00", "Europe/Paris"]:
            with self.subTest(field=value):
                self.assertIn(value, report)
        job.duration = ""
        self.assertIn("February 2027 / Not provided", self.render([job]))

    def test_unverified_and_closed_jobs_are_not_presented_as_open(self):
        opened = RawJob(company="OpenCo", title="Open internship", status="open")
        unknown = RawJob(company="UnknownCo", title="Unknown internship", status="unverified", verification_evidence="Application page unavailable")
        closed = RawJob(company="ClosedCo", title="Closed internship", status="closed")
        report = self.render([opened, unknown, closed])
        open_section, unknown_section = report.split("## Unverified", 1)
        self.assertIn("OpenCo", open_section)
        self.assertNotIn("UnknownCo", open_section)
        self.assertIn("UnknownCo", unknown_section)
        self.assertNotIn("ClosedCo", report)

    def test_complete_empty_differs_from_failed_or_partial_search(self):
        complete = self.render()
        partial = self.render(sources=[SourceResult("Blocked site", "https://blocked.test", complete=False, error="HTTP 503 after retries")])
        self.assertIn("No matching internships found", complete)
        self.assertIn("COMPLETE for configured sources", complete)
        self.assertIn("INCOMPLETE", partial)
        self.assertIn("does not establish that no matching internships exist", partial)
        self.assertIn("Blocked site", partial)
        self.assertIn("HTTP 503 after retries", partial)

    def test_verification_failure_marks_report_incomplete(self):
        report = self.render(verification_incomplete=True)
        self.assertIn("INCOMPLETE", report)

    def test_atomic_write_replaces_and_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "internship.md"
            path.write_text("Old report")
            atomic_write(path, "New report")
            self.assertEqual(path.read_text(), "New report")
            self.assertEqual(list(Path(temp).iterdir()), [path])

    def test_failed_atomic_replace_preserves_previous_report_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "internship.md"
            path.write_text("Old report with useful results")
            with patch("internship_finder.report.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    atomic_write(path, "New report")
            self.assertEqual(path.read_text(), "Old report with useful results")
            self.assertEqual(list(Path(temp).iterdir()), [path])

    def test_entire_failure_preserves_results_and_original_timestamps(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "internship.md"
            original = "# C++ internships\nLast update: 2026-10-31T08:00:00+01:00\nPrevious internship result\n"
            path.write_text(original)
            preserve_stale(path, NOW, "All sources failed")
            first = path.read_text()
            self.assertTrue(first.endswith(original))
            self.assertIn("STALE", first)
            self.assertIn("All sources failed", first)
            preserve_stale(path, NOW, "All sources still failing")
            second = path.read_text()
            self.assertTrue(second.endswith(original))
            self.assertEqual(second.count("<!-- stale -->"), 1)
            self.assertNotIn("All sources failed", second)

    def test_first_run_total_failure_does_not_claim_zero_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "internship.md"
            preserve_stale(path, NOW, "No source was reachable")
            report = path.read_text()
            self.assertIn("No successful results are available yet", report)
            self.assertIn("not a zero-match result", report)
            self.assertIn("STALE", report)

    def test_report_sanitizes_markup_and_unsafe_application_urls(self):
        job = RawJob(company="<script>evil</script>", title="[Fake](bad)\nInjected", url="javascript:alert(1)", status="open")
        report = self.render([job])
        self.assertNotIn("<script>", report)
        self.assertNotIn("javascript:", report)
        self.assertIn("&lt;script&gt;", report)


if __name__ == "__main__":
    unittest.main()
