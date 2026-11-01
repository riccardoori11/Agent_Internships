"""Conservative availability checks: an HTTP 200 alone never means open."""
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
import logging
import re
from urllib.parse import urljoin, urlparse

from .models import RawJob, SourceError

LOG = logging.getLogger(__name__)
ATS_HOSTS = ("myworkdayjobs.com", "greenhouse.io", "lever.co", "smartrecruiters.com", "ashbyhq.com")
CLOSED = re.compile(
    r"(?:this (?:job|position|vacancy|posting) (?:is |has been )?(?:closed|filled|no longer available)|"
    r"(?:job|position|vacancy|posting) (?:is )?no longer (?:available|accepting applications)|"
    r"no longer accepting applications|not accepting applications|"
    r"(?:offre|poste|annonce) (?:n['’]est plus disponible|pourvu[e]?|cl[oô]tur[ée]+|expir[ée]+)|"
    r"(?:cette|l['’])offre n['’]existe plus|(?:recrutement|candidatures?) (?:est |sont )?termin[ée]+)",
    re.IGNORECASE,
)
CHALLENGE = re.compile(r"verify (?:that )?you are human|access denied|just a moment|captcha|enable javascript and cookies", re.I)


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []
        self.inputs = []
        self.forms = []
        self.form_inputs = []
        self._current_form = None
        self.script_data = []
        self._suppressed = 0
        self._jsonld = False
        self._json = []
        self._anchor = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self._suppressed += 1
            if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
                self._jsonld = True
                self._json = []
        if tag == "a":
            self._anchor = [attrs.get("href", ""), []]
        if tag == "form":
            self.forms.append(attrs)
            self.form_inputs.append([])
            self._current_form = len(self.forms) - 1
        if tag == "input":
            self.inputs.append(attrs)
            if self._current_form is not None:
                self.form_inputs[self._current_form].append(attrs)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self._suppressed = max(0, self._suppressed - 1)
            if self._jsonld:
                try:
                    self.script_data.append(json.loads("".join(self._json)))
                except (ValueError, TypeError):
                    pass
                self._jsonld = False
        if tag == "a" and self._anchor:
            self.links.append((self._anchor[0], " ".join(self._anchor[1])))
            self._anchor = None
        if tag == "form":
            self._current_form = None

    def handle_data(self, data):
        if self._jsonld:
            self._json.append(data)
        if not self._suppressed:
            self.text.append(data)
            if self._anchor:
                self._anchor[1].append(data)

    @property
    def visible(self):
        return " ".join(" ".join(self.text).split())


def _postings(value):
    if isinstance(value, list):
        for item in value:
            yield from _postings(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if "JobPosting" in ([types] if isinstance(types, str) else types):
            yield value
        yield from _postings(value.get("@graph", []))


def _expired(value, now):
    if not value:
        return False
    try:
        deadline = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # A date-only deadline remains valid through that calendar day.
        if len(str(value)) == 10:
            return deadline.date() < now.date()
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=now.tzinfo)
        return deadline < now
    except (ValueError, TypeError):
        return False


def _is_employer_url(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in ATS_HOSTS)


def _application_form(page):
    """Recognize candidate forms, excluding newsletter/email signups."""
    for form, form_inputs in zip(page.forms, page.form_inputs):
        # HTML permits inputs outside a form to reference it by its id.
        external = [item for item in page.inputs if form.get("id") and item.get("form") == form["id"]]
        usable = [item for item in form_inputs + external if "disabled" not in item]
        inputs = " ".join(" ".join(str(item.get(key, "")) for key in ("name", "id", "type", "autocomplete")) for item in usable).lower()
        has_email = bool(re.search(r"email|e-mail", inputs))
        has_cv = bool(re.search(r"resume|(?:^|\s)cv(?:\s|$)|curriculum", inputs))
        has_name = bool(re.search(r"first.?name|last.?name|prenom|given-name|family-name", inputs))
        form_identity = " ".join(str(form.get(key, "")) for key in ("id", "name", "action")).lower()
        application_identity = bool(re.search(r"application|applicant|candidate|apply|candidature", form_identity))
        # A footer contact form can have name/email on a perfectly valid job
        # page. Require CV fields or an explicitly identified application form.
        if has_email and (has_cv or has_name and application_identity):
            return True
    return False


def _contains_title(page, title):
    normal = lambda value: " ".join(re.findall(r"\w+", value.casefold()))
    wanted = normal(title)
    return bool(wanted and wanted in normal(page.visible))


def _expired_posting(page, job, now):
    for obj in page.script_data:
        for posting in _postings(obj):
            title = posting.get("title", "")
            # A recommendation's deadline cannot close the requested job.
            if title and re.sub(r"\W+", "", str(title).casefold()) != re.sub(r"\W+", "", job.title.casefold()):
                continue
            if _expired(posting.get("validThrough"), now):
                return True
    return False


def verify_job(job: RawJob, client, now=None):
    """Update availability using a live employer listing/application response.

    Published ATS-board membership plus a real candidate form confirms open.
    Expiration and explicit closure override positive signals. JS-only shells,
    aggregators, blocking and errors are retained as unverified.
    """
    now = now or datetime.now(timezone.utc)
    job.status = "unverified"
    job.verified_at = now.isoformat()
    job.verification_evidence = "Availability has not been confirmed."
    if _expired(job.metadata.get("valid_through"), now):
        job.status = "closed"
        job.verification_evidence = "The listing's stated application deadline has passed."
        return job
    target = job.apply_url or job.url
    if not target or urlparse(target).scheme not in {"http", "https"}:
        job.verification_evidence = "No HTTP application or listing URL was supplied."
        return job
    try:
        response = client.fetch(target)
        captured = response.headers.get("x-snapshot-at")
        if captured:
            job.verified_at = captured
            job.metadata["snapshot_provenance"] = response.headers.get("x-snapshot-provenance", "")
        page = Page(response.text)
        if CLOSED.search(page.visible):
            job.status = "closed"
            job.verification_evidence = "The application page explicitly reports that applications are closed."
            return job
        if _expired_posting(page, job, now):
            job.status = "closed"
            job.verification_evidence = "The employer's structured listing has an expired validThrough date."
            return job
        if CHALLENGE.search(page.visible) and not _application_form(page):
            job.verification_evidence = "The application page returned a bot challenge or access restriction."
            return job
        same_host = urlparse(response.url).hostname == urlparse(target).hostname
        employer = _is_employer_url(response.url) or bool(job.metadata.get("employer_source") and same_host)
        if employer and _application_form(page):
            # Prevent a redirect to an unrelated generic careers/contact form.
            path = urlparse(response.url).path
            original_path = urlparse(target).path
            related = path == original_path or path.startswith(original_path.rstrip("/") + "/")
            if related and (_contains_title(page, job.title) or same_host and job.metadata.get("active_board")):
                job.status = "open"
                job.apply_url = response.url
                if job.url != response.url:
                    job.metadata["discovery_url"] = job.url
                job.url = response.url
                job.metadata["employer_original"] = True
                job.verification_evidence = "A candidate application form is available on the employer's job-specific page."
                return job
        # Follow one explicit application action; never submit an application.
        for href, label in page.links:
            if not re.fullmatch(r"\s*(?:apply(?: for this job| now)?|postuler|je suis intéressé\(e\))\s*", label, re.I):
                continue
            destination = urljoin(response.url, unescape(href))
            if destination == response.url or urlparse(destination).scheme not in {"http", "https"}:
                continue
            # Links leaving an aggregator must lead to a recognized employer ATS;
            # arbitrary link destinations never become employer evidence.
            if not (_is_employer_url(destination) or employer and urlparse(destination).hostname == urlparse(response.url).hostname):
                continue
            follow = client.fetch(destination)
            application = Page(follow.text)
            if CLOSED.search(application.visible):
                job.status = "closed"
                job.verification_evidence = "The employer application destination reports that the job is closed."
                return job
            if _expired_posting(application, job, now):
                job.status = "closed"
                job.verification_evidence = "The application destination has an expired structured application deadline."
                return job
            target_path = urlparse(destination).path.rstrip("/")
            final_path = urlparse(follow.url).path.rstrip("/")
            related = bool(target_path and (target_path == final_path or final_path.startswith(target_path + "/")))
            if related and _application_form(application) and _contains_title(application, job.title) and not CHALLENGE.search(application.visible):
                job.status = "open"
                job.apply_url = follow.url
                if job.url != follow.url:
                    job.metadata["discovery_url"] = job.url
                job.url = follow.url
                job.metadata["employer_original"] = True
                job.verification_evidence = "The employer application link opens a candidate application form."
                return job
        job.verification_evidence = "The page loaded, but a job-specific employer application form could not be confirmed."
    except SourceError as exc:
        # 404/410 is a positive removal signal only at an employer source.
        if re.search(r"HTTP (?:404|410):", str(exc)) and (_is_employer_url(target) or job.metadata.get("employer_source")):
            job.status = "closed"
            job.verification_evidence = "The employer application/listing returns HTTP 404 or 410."
        else:
            job.verification_evidence = f"Application verification failed: {exc}"
        LOG.warning("Verification %s %s: %s", job.status, target, exc)
    return job
