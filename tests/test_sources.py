import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from internship_finder.http import HttpClient, HttpResponse
from internship_finder.models import RawJob, SourceError
from internship_finder.sources import collect_source, collect_sources, enrich_job, jobposting_objects, plain


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        return self.responses[url]

    def fetch(self, url):
        self.urls.append(url)
        return HttpResponse(url, 200, self.responses[url], {})


class SourceTests(unittest.TestCase):
    def test_lever_exhausts_pages_including_short_pages(self):
        base = "https://api.lever.co/v0/postings/example?mode=json&limit=100&skip="
        client = FakeClient({base + "0": [{"id": "one", "text": "C++ Intern"}], base + "1": [{"id": "two", "text": "Stage C++"}], base + "2": []})
        result = collect_source({"name": "test", "kind": "lever", "board": "example"}, client)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(result.pages, 3)

    def test_repeated_page_marks_partial(self):
        base = "https://api.lever.co/v0/postings/example?mode=json&limit=100&skip="
        client = FakeClient({base + "0": [{"id": "one"}], base + "1": [{"id": "one"}]})
        result = collect_source({"name": "test", "kind": "lever", "board": "example"}, client)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.jobs), 1)
        self.assertIn("repeated", result.error)

    def test_html_follows_next_and_fetches_details(self):
        posting = '<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": "Stage C++", "hiringOrganization": {"name": "Example"}, "jobLocation": {"address": {"addressLocality": "Paris", "addressCountry": "France"}}}) + '</script>'
        client = FakeClient({"https://example.org/search": '<a href="/jobs/1">one</a><a rel="next" href="?page=2">Next</a>', "https://example.org/search?page=2": '<a href="/jobs/2">two</a>', "https://example.org/jobs/1": posting, "https://example.org/jobs/2": posting})
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search", "queries": ["stage"]}, client)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(result.pages, 2)
        self.assertEqual(result.jobs[0].location, "Paris, France")

    def test_javascript_shell_is_failure_not_zero(self):
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org", "queries": ["stage"]}, FakeClient({"https://example.org": '<div id="root"></div>'}))
        self.assertFalse(result.complete)
        self.assertIn("JavaScript", result.error)

    def test_numbered_html_pagination_is_followed(self):
        client = FakeClient({"https://example.org/search": '<p>0 jobs</p><a href="?page=2">2</a>', "https://example.org/search?page=2": '<p>0 jobs</p><a href="?page=1">1</a><a aria-current="page" href="?page=2">2</a>'})
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search", "queries": ["stage", "intern"]}, client)
        self.assertTrue(result.complete)
        self.assertEqual(result.pages, 2)

    def test_unexhausted_page_counter_marks_incomplete(self):
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search"}, FakeClient({"https://example.org/search": "0 jobs Page 1 of 3"}))
        self.assertFalse(result.complete)
        self.assertIn("more pages", result.error)

    def test_javascript_pagination_control_never_marks_partial_page_complete(self):
        posting = '<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": "Stage C++", "url": "https://example.org/jobs/1"}) + '</script>'
        for control in ['<button><span>Load more</span></button>', '<div role="button" aria-label="Afficher plus"></div>', '<button>Voir plus d’offres</button>', '<button>Next</button>', '<button>Suivant</button>']:
            with self.subTest(control=control):
                result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search"}, FakeClient({"https://example.org/search": posting + control}))
                self.assertFalse(result.complete)
                self.assertEqual(len(result.jobs), 1)
                self.assertIn("JavaScript pagination", result.error)

    def test_disabled_load_more_button_is_exhausted(self):
        client = FakeClient({"https://example.org/search": '<p>0 jobs</p><button disabled>Load more</button>'})
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search"}, client)
        self.assertTrue(result.complete)

    def test_advertised_job_count_larger_than_collected_marks_incomplete(self):
        posting = '<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": "Stage C++", "url": "https://example.org/jobs/1"}) + '</script>'
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search"}, FakeClient({"https://example.org/search": '<p>100 jobs</p>' + posting}))
        self.assertFalse(result.complete)
        self.assertIn("100 jobs but only 1", result.error)

    def test_advertised_job_count_counts_all_pages(self):
        def posting(number):
            return '<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": "Stage C++", "url": "https://example.org/jobs/" + str(number)}) + '</script>'
        client = FakeClient({"https://example.org/search": '<p>2 jobs</p>' + posting(1) + '<a rel="next" href="?page=2">Next</a>', "https://example.org/search?page=2": '<p>2 jobs</p>' + posting(2)})
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search"}, client)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.jobs), 2)

    def test_one_hundred_jobs_never_matches_zero_jobs_empty_state(self):
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search"}, FakeClient({"https://example.org/search": '<p>100 jobs</p><a href="/unsupported/1">Job</a>'}))
        self.assertFalse(result.complete)
        self.assertIn("No jobs or explicit empty-state", result.error)

    def test_numbered_pagination_never_skips_intermediate_page(self):
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search"}, FakeClient({"https://example.org/search": '<p>0 jobs</p><a href="?page=3">3</a>'}))
        self.assertFalse(result.complete)
        self.assertIn("skips an intermediate", result.error)

    def test_location_placeholder_tracks_scope_without_mutating_config(self):
        source = {"name": "test", "kind": "html_search", "url": "https://example.org/search?l={location}"}
        for scope, encoded in [("paris", "Paris%2C%20France"), ("ile-de-france", "%C3%8Ele-de-France%2C%20France")]:
            with self.subTest(scope=scope):
                url = "https://example.org/search?l=" + encoded
                client = FakeClient({url: "0 jobs"})
                result = collect_sources({"search": {"scope": scope}, "sources": [source]}, client)
                self.assertTrue(result[0].complete)
                self.assertEqual(client.urls, [url])
                self.assertEqual(source["url"], "https://example.org/search?l={location}")

    def test_hidden_js_no_results_translation_is_not_empty_state(self):
        result = collect_source({"name": "test", "kind": "html_search", "url": "https://example.org/search"}, FakeClient({"https://example.org/search": '<script>const label="no results";</script><div id="root"></div>'}))
        self.assertFalse(result.complete)

    def test_greenhouse_never_uses_update_date_as_posting_date(self):
        url = "https://boards-api.greenhouse.io/v1/boards/test/jobs?content=true"
        result = collect_source({"name": "test", "kind": "greenhouse", "board": "test"}, FakeClient({url: {"jobs": [{"title": "Stage", "updated_at": "2026-11-01"}], "meta": {"total": 1}}}))
        self.assertTrue(result.complete)
        self.assertEqual(result.jobs[0].posted_at, "")

    def test_null_requisition_id_is_unknown_not_a_shared_identifier(self):
        url = "https://boards-api.greenhouse.io/v1/boards/test/jobs?content=true"
        result = collect_source({"name": "test", "kind": "greenhouse", "board": "test"}, FakeClient({url: {"jobs": [{"id": 123, "requisition_id": None, "title": "Stage"}]}}))
        self.assertTrue(result.complete)
        self.assertEqual(result.jobs[0].metadata["requisition_id"], "")
        self.assertEqual(result.jobs[0].metadata["identifier"], "123")

    def test_plain_decodes_greenhouse_escaped_html(self):
        self.assertEqual(plain("&lt;p&gt;Develop C++&lt;/p&gt;"), "Develop C++")

    def test_explicit_french_start_duration_and_hybrid(self):
        job = enrich_job(RawJob(title="Stage C++", description="Stage de 6 mois à partir de février 2027. Travail hybride."))
        self.assertEqual(job.duration, "6 mois")
        self.assertEqual(job.start_date, "février 2027")
        self.assertEqual(job.work_arrangement, "Hybrid")
        self.assertIn("à partir de", job.metadata["field_evidence"]["start_date"])

    def test_explicit_english_start_duration(self):
        job = enrich_job(RawJob(title="Software Intern", description="Duration: 4 to 6 months. Start date: January 2027."))
        self.assertEqual(job.duration, "4 to 6 months")
        self.assertEqual(job.start_date, "January 2027")

    def test_never_infer_start_from_title_or_generic_company_history(self):
        job = enrich_job(RawJob(title="Internship 2027", description="Founded January 1997. Study hybrid quantum algorithms."))
        self.assertEqual(job.duration, "")
        self.assertEqual(job.start_date, "")
        self.assertEqual(job.work_arrangement, "")

    def test_explicit_employer_application_route_preferred(self):
        body = '<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": "Stage C++", "url": "https://example.org/jobs/1"}) + '</script><a href="https://jobs.lever.co/example/req/apply">Apply on employer site</a>'
        result = collect_source({"kind": "jobpage", "name": "test", "url": "https://example.org/jobs/1"}, FakeClient({"https://example.org/jobs/1": body}))
        self.assertEqual(result.jobs[0].apply_url, "https://jobs.lever.co/example/req/apply")
        self.assertFalse(result.jobs[0].metadata["employer_original"])


class HttpTests(unittest.TestCase):
    @patch("internship_finder.http.time.sleep")
    @patch("internship_finder.http.urllib.request.urlopen")
    def test_temporary_failure_retries_then_fails(self, urlopen, sleep):
        urlopen.side_effect = urllib.error.URLError("temporary DNS")
        with self.assertRaises(SourceError):
            HttpClient(retries=2).get("https://example.org")
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("internship_finder.http.time.sleep")
    @patch("internship_finder.http.urllib.request.urlopen")
    def test_permanent_http_error_is_not_retried(self, urlopen, sleep):
        urlopen.side_effect = urllib.error.HTTPError("https://example.org", 404, "gone", {}, None)
        with self.assertRaisesRegex(SourceError, "HTTP 404"):
            HttpClient(retries=3).get("https://example.org")
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_snapshot_missing_url_never_calls_network(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "manifest.json").write_text("{}")
            client = HttpClient(snapshot_dir=directory)
            with patch("internship_finder.http.urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(SourceError, "No snapshot"):
                    client.get("https://example.org")
                urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
