from datetime import datetime, timezone
import unittest

from internship_finder.http import HttpResponse
from internship_finder.models import RawJob, SourceError
from internship_finder.verification import verify_job


NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
URL = "https://jobs.lever.co/company/abc"


class Client:
    def __init__(self, body="", error=None, final_url=URL):
        self.body, self.error, self.final_url = body, error, final_url

    def fetch(self, url):
        if self.error:
            raise SourceError(self.error)
        return HttpResponse(self.final_url, 200, self.body, {})


class RoutingClient:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        result = self.pages[url]
        if isinstance(result, HttpResponse):
            return result
        return HttpResponse(url, 200, result, {})


class VerificationTests(unittest.TestCase):
    def job(self, **kwargs):
        return RawJob(title="C++ Internship", url=URL, metadata={"active_board": True}, **kwargs)

    def test_http_200_without_form_is_unverified(self):
        result = verify_job(self.job(), Client("<h1>C++ Internship</h1>"), NOW)
        self.assertEqual(result.status, "unverified")

    def test_application_form_confirms_current_ats_job(self):
        body = '<h1>C++ Internship</h1><form><input name="email"><input name="resume"></form>'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "open")

    def test_newsletter_form_does_not_confirm(self):
        body = '<h1>C++ Internship</h1><form><input name="email"></form>'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "unverified")

    def test_footer_contact_form_does_not_confirm(self):
        body = '<h1>C++ Internship</h1><footer><form action="/contact"><input name="email"><input name="first_name"></form></footer>'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "unverified")

    def test_explicit_application_form_with_name_and_email_confirms(self):
        body = '<h1>C++ Internship</h1><form id="application_form"><input name="email"><input name="first_name"></form>'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "open")

    def test_expiration_overrides_form(self):
        body = '<script type="application/ld+json">{"@type":"JobPosting","validThrough":"2026-10-31"}</script><form><input name="email"><input name="resume"></form>'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "closed")

    def test_closed_marker_overrides_form(self):
        body = '<h1>This position is no longer available</h1><form><input name="email"><input name="resume"></form>'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "closed")

    def test_temporary_failure_unverified(self):
        self.assertEqual(verify_job(self.job(), Client(error="HTTP 503: failed"), NOW).status, "unverified")

    def test_employer_404_closed(self):
        self.assertEqual(verify_job(self.job(), Client(error="HTTP 404: removed"), NOW).status, "closed")

    def test_generic_redirect_not_confirmed(self):
        body = '<form><input name="email"><input name="resume"></form>'
        self.assertEqual(verify_job(self.job(), Client(body, final_url="https://jobs.lever.co/company"), NOW).status, "unverified")

    def test_similar_unrelated_job_path_not_confirmed(self):
        body = '<form><input name="email"><input name="resume"></form>'
        self.assertEqual(verify_job(self.job(), Client(body, final_url=URL + '-other'), NOW).status, "unverified")

    def test_fields_in_distinct_forms_do_not_combine(self):
        body = '<h1>C++ Internship</h1><form><input name="email"></form><form><input name="first_name"></form>'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "unverified")

    def test_disabled_candidate_fields_do_not_confirm(self):
        body = '<h1>C++ Internship</h1><form><input name="email"><input name="resume" disabled></form>'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "unverified")

    def test_explicit_form_attribute_supported(self):
        body = '<h1>C++ Internship</h1><form id="apply"></form><input form="apply" name="email"><input form="apply" name="resume">'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "open")

    def linked_client(self, body, final_url=None):
        destination = URL + '/apply'
        application = HttpResponse(final_url or destination, 200, body, {})
        return RoutingClient({URL: '<h1>C++ Internship</h1><a href="/company/abc/apply">Apply</a>', destination: application})

    def test_linked_unrelated_form_does_not_confirm(self):
        body = '<h1>Contact us</h1><form><input name="email"><input name="first_name"></form>'
        self.assertEqual(verify_job(self.job(), self.linked_client(body), NOW).status, "unverified")

    def test_linked_form_redirect_to_contact_not_confirmed(self):
        body = '<h1>C++ Internship</h1><form><input name="email"><input name="resume"></form>'
        client = self.linked_client(body, "https://jobs.lever.co/contact")
        self.assertEqual(verify_job(self.job(), client, NOW).status, "unverified")

    def test_linked_matching_application_form_confirms(self):
        body = '<h1>C++ Internship</h1><form><input name="email"><input name="resume"></form>'
        self.assertEqual(verify_job(self.job(), self.linked_client(body), NOW).status, "open")

    def test_linked_expired_form_closed(self):
        body = '<h1>C++ Internship</h1><script type="application/ld+json">{"@type":"JobPosting","title":"C++ Internship","validThrough":"2026-10-31"}</script><form><input name="email"><input name="resume"></form>'
        self.assertEqual(verify_job(self.job(), self.linked_client(body), NOW).status, "closed")

    def test_linked_closed_form_closed(self):
        body = '<h1>C++ Internship</h1><p>This job has been closed</p><form><input name="email"><input name="resume"></form>'
        self.assertEqual(verify_job(self.job(), self.linked_client(body), NOW).status, "closed")

    def test_recommended_expired_posting_does_not_close_job(self):
        body = '<h1>C++ Internship</h1><script type="application/ld+json">{"@type":"JobPosting","title":"Other Job","validThrough":"2026-10-31"}</script><form><input name="email"><input name="resume"></form>'
        self.assertEqual(verify_job(self.job(), Client(body), NOW).status, "open")


if __name__ == "__main__":
    unittest.main()
