"""Public ATS feeds and configurable paginated HTML discovery.

No adapter truncates a feed at a chosen number of results. Page sizes are transport
sizes; pagination continues to the source's end. Repeated pages fail visibly.
"""
import html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from .models import RawJob, SourceError, SourceResult

LOG = logging.getLogger(__name__)


def plain(value):
    text = html.unescape(html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


class PageParser(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.scripts = []
        self.controls = []
        self._controls = []
        self.visible_text = []
        self._suppressed = 0
        self._anchor = None
        self._script = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for control in self._controls:
            if control[0] == tag:
                control[3] += 1
        if tag == "button" or attrs.get("role") == "button":
            if tag == "input":
                self.controls.append([attrs, attrs.get("value", "")])
            else:
                self._controls.append([tag, attrs, "", 1])
        if tag in {"script", "style"}:
            self._suppressed += 1
        if tag == "a":
            self._anchor = [attrs, ""]
        if tag == "link" and "next" in attrs.get("rel", "").split():
            self.links.append([attrs, ""])
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self._script = ""

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor[1] += data
        if self._script is not None:
            self._script += data
        if not self._suppressed:
            self.visible_text.append(data)
            for control in self._controls:
                control[2] += data

    def handle_endtag(self, tag):
        for control in self._controls[:]:
            if control[0] == tag:
                control[3] -= 1
                if control[3] == 0:
                    self.controls.append([control[1], control[2]])
                    self._controls.remove(control)
        if tag == "a" and self._anchor is not None:
            self.links.append(self._anchor)
            self._anchor = None
        if tag == "script" and self._script is not None:
            self.scripts.append(self._script)
            self._script = None
        if tag in {"script", "style"}:
            self._suppressed = max(0, self._suppressed - 1)


def jobposting_objects(text):
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            kind = value.get("@type", [])
            if "JobPosting" in ([kind] if isinstance(kind, str) else kind):
                yield value
            for key in ("@graph", "itemListElement", "item"):
                if key in value:
                    yield from walk(value[key])
    for script in PageParser(text).scripts:
        try:
            yield from walk(json.loads(script))
        except (json.JSONDecodeError, TypeError):
            LOG.warning("Malformed JSON-LD script ignored")


def _location(value):
    if isinstance(value, list):
        return "; ".join(filter(None, (_location(v) for v in value)))
    if isinstance(value, dict):
        address = value.get("address", value)
        if isinstance(address, str):
            return address
        return ", ".join(str(address[k]) for k in ("addressLocality", "addressRegion", "postalCode", "addressCountry") if address.get(k))
    return str(value or "")


def from_jsonld(value, url, source):
    organization = value.get("hiringOrganization", {})
    company = organization.get("name", "") if isinstance(organization, dict) else str(organization)
    job = RawJob(title=plain(value.get("title")), company=plain(company or source.get("company", "")), location=_location(value.get("jobLocation")), description=plain(value.get("description")), url=value.get("url") or url, apply_url=value.get("url") or url, posted_at=value.get("datePosted", ""), contract_type=plain(value.get("employmentType")), source=source["name"], work_arrangement=plain(value.get("jobLocationType")), start_date=plain(value.get("jobStartDate")))
    identifier = value.get("identifier", "")
    if isinstance(identifier, dict):
        identifier = identifier.get("value", "")
    job.metadata.update({"valid_through": value.get("validThrough", ""), "employer_source": bool(source.get("employer", False)), "employer_original": bool(source.get("employer", False)), "identifier": identifier, "requisition_id": identifier, "retrieved_at": datetime.now(timezone.utc).isoformat()})
    structured_duration = value.get("jobDuration") or value.get("employmentDuration")
    if isinstance(structured_duration, str):
        job.duration = structured_duration
    return job


MONTH = r"(?:jan(?:uary|vier)?|f[ée]v(?:rier)?|feb(?:ruary)?|mar(?:ch|s)?|apr(?:il)?|avril|mai|may|juin|june|juil(?:let)?|july|ao[uû]t|aug(?:ust)?|sept(?:ember|embre)?|oct(?:ober|obre)?|nov(?:ember|embre)?|d[ée]c(?:ember|embre)?)\.?"
DATE = rf"(?:(?:\d{{1,2}}\s+)?{MONTH}\s+20\d{{2}}|20\d{{2}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/.]\d{{1,2}}[/.]20\d{{2}}|as soon as possible|d[èe]s que possible|imm[ée]diat(?:ement)?)"
DURATION = r"(?:\d{1,2}|three|four|five|six|twelve|trois|quatre|cinq|six|douze)(?:\s*(?:-|–|to|[àa])\s*\d{1,2})?\s*(?:months?|mois|weeks?|semaines?)"


def enrich_job(job):
    """Copy explicit field evidence; never infer dates from a cohort year."""
    text = plain(job.title + ". " + job.description)
    evidence = job.metadata.setdefault("field_evidence", {})
    if not job.duration:
        title_duration = re.search(DURATION, job.title, re.I) if re.search(r"\b(?:stage|internship)\b", job.title, re.I) else None
        if title_duration:
            job.duration = title_duration[0]
            evidence["duration"] = job.title
        patterns = [rf"(?:dur[ée]e|duration|placement length|internship length)\s*(?:du stage)?\s*[:=-]?\s*({DURATION})", rf"(?:stage|internship)\s*(?:de|of|for|d['’]une dur[ée]e de)?\s*({DURATION})", rf"({DURATION})\s*(?:internship|stage)"]
        for pattern in patterns:
            if job.duration:
                break
            found = re.search(pattern, text, re.I)
            if found:
                job.duration = found[1]
                evidence["duration"] = found[0]
                break
    if not job.start_date:
        patterns = [rf"(?:start date|starting date|date de d[ée]but|d[ée]but du stage|d[ée]marrage|d[ée]but)\s*[:=-]?\s*({DATE})", rf"(?:[àa] partir (?:de|du|d['’])|starting (?:in|on)|starts? (?:in|on)|beginning (?:in|on))\s*({DATE})"]
        for pattern in patterns:
            found = re.search(pattern, text, re.I)
            if found:
                job.start_date = found[1]
                evidence["start_date"] = found[0]
                break
    if not job.work_arrangement:
        patterns = [(r"\b(?:no remote work|does not (?:offer|allow) remote work|pas de t[ée]l[ée]travail|on[- ]site (?:work|role|position)|work(?:ing)? on[- ]site|travail sur site)\b", "On-site"), (r"\b(?:hybrid (?:work(?:ing)?|role|position|mode)|(?:work(?:ing)?|mode|travail) hybrid[e]?|t[ée]l[ée]travail partiel)\b", "Hybrid"), (r"\b(?:fully remote|100\s*%\s*(?:remote|t[ée]l[ée]travail)|(?:travail|poste) (?:en )?t[ée]l[ée]travail complet)\b", "Remote")]
        for pattern, arrangement in patterns:
            found = re.search(pattern, text, re.I)
            if found:
                job.work_arrangement = arrangement
                evidence["work_arrangement"] = found[0]
                break
    return job


def _application_link(job, response):
    targets = []
    for attrs, label in PageParser(response.text).links:
        href = attrs.get("href", "")
        target = urljoin(response.url, href)
        host = (urlparse(target).hostname or "").lower()
        if re.search(r"\b(?:apply|postuler|candidater|int[ée]ress[ée])\b", label, re.I) and any(host == suffix or host.endswith("." + suffix) for suffix in ("myworkdayjobs.com", "greenhouse.io", "lever.co", "smartrecruiters.com", "ashbyhq.com")):
            targets.append(target)
    targets = list(dict.fromkeys(targets))
    if len(targets) == 1:
        job.apply_url = targets[0]
        job.metadata["original_application_url"] = targets[0]


def _api_metadata(job, api_url):
    job.metadata.update({"active_board": True, "employer_source": True, "employer_original": True, "api_url": api_url, "retrieved_at": datetime.now(timezone.utc).isoformat()})
    job.metadata.setdefault("requisition_id", job.metadata.get("identifier", ""))
    return job


def greenhouse(source, client, result):
    board = quote(source["board"], safe="")
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    data = client.get_json(url)
    if not isinstance(data.get("jobs"), list):
        raise SourceError("Greenhouse response has no jobs array")
    if data.get("meta", {}).get("total", len(data["jobs"])) != len(data["jobs"]):
        raise SourceError("Greenhouse response does not contain its advertised total")
    result.url = url
    result.pages += 1  # This endpoint returns the complete board in one response.
    for value in data["jobs"]:
        job = RawJob(title=plain(value.get("title")), company=source.get("company", source["board"]), location=plain(value.get("location", {}).get("name")), description=plain(value.get("content")), url=value.get("absolute_url", ""), apply_url=value.get("absolute_url", ""), source=source["name"], posted_at=value.get("first_published", ""))
        # updated_at is not a posting date.
        job.metadata["identifier"] = str(value.get("id") or "")
        job.metadata["requisition_id"] = str(value.get("requisition_id") or "")
        for field in value.get("metadata") or []:
            if field.get("value") and re.search("employment|contract|type", field.get("name", ""), re.I):
                job.contract_type += " " + str(field["value"])
        result.jobs.append(_api_metadata(job, url))


def lever(source, client, result):
    offset = 0
    seen = set()
    host = source.get("host", "https://api.lever.co").rstrip("/")
    while True:
        url = f"{host}/v0/postings/{quote(source['board'], safe='')}?mode=json&limit=100&skip={offset}"
        values = client.get_json(url)
        if not isinstance(values, list):
            raise SourceError("Lever response is not an array")
        result.pages += 1
        if not values:
            break
        for value in values:
            identifier = value.get("id")
            if identifier in seen:
                raise SourceError("Lever repeated a posting while paginating")
            seen.add(identifier)
            category = value.get("categories", {})
            description = value.get("descriptionPlain") or value.get("description", "")
            description += " " + " ".join(plain(p.get("text", "")) + " " + plain(p.get("content", "")) for p in value.get("lists", []))
            description += " " + (value.get("additionalPlain") or value.get("additional", ""))
            posted = ""
            if value.get("createdAt"):
                posted = datetime.fromtimestamp(value["createdAt"] / 1000, timezone.utc).isoformat()
            job = RawJob(title=plain(value.get("text")), company=source.get("company", source["board"]), location=plain(category.get("location")), description=plain(description), url=value.get("hostedUrl", ""), apply_url=value.get("applyUrl") or value.get("hostedUrl", ""), posted_at=posted, source=source["name"], contract_type=plain(category.get("commitment")), work_arrangement=plain(value.get("workplaceType")))
            job.metadata["identifier"] = identifier
            result.jobs.append(_api_metadata(job, url))
        offset += len(values)


def ashby(source, client, result):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(source['board'], safe='')}?includeCompensation=false"
    data = client.get_json(url)
    if not isinstance(data.get("jobs"), list):
        raise SourceError("Ashby response has no jobs array")
    result.url = url
    result.pages += 1
    for value in data["jobs"]:
        if value.get("isListed") is False:
            continue
        job = RawJob(title=plain(value.get("title")), company=source.get("company", source["board"]), location=plain(value.get("location")), description=plain(value.get("descriptionPlain") or value.get("descriptionHtml")), url=value.get("jobUrl", ""), apply_url=value.get("applyUrl") or value.get("jobUrl", ""), source=source["name"], posted_at=value.get("publishedAt", ""), contract_type=plain(value.get("employmentType")), work_arrangement="Remote" if value.get("isRemote") else "")
        job.metadata["identifier"] = value.get("id", "")
        result.jobs.append(_api_metadata(job, url))


def smartrecruiters(source, client, result):
    offset = 0
    seen = set()
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(source['board'], safe='')}/postings"
    while True:
        params = {"limit": 100, "offset": offset}
        if source.get("country"):
            params["country"] = source["country"]
        url = base + "?" + urlencode(params)
        data = client.get_json(url)
        values = data.get("content")
        if not isinstance(values, list) or "totalFound" not in data:
            raise SourceError("SmartRecruiters response lacks content/totalFound")
        result.pages += 1
        if not values:
            if offset < int(data["totalFound"]):
                raise SourceError("SmartRecruiters ended before advertised total")
            break
        for value in values:
            identifier = value.get("id")
            if identifier in seen:
                raise SourceError("SmartRecruiters repeated posting during pagination")
            seen.add(identifier)
            # Fetch details only for internship candidates, never arbitrary result caps.
            contract = plain(value.get("typeOfEmployment", {}).get("label"))
            title = plain(value.get("name"))
            if not re.search(r"\b(stage|stagiaire|internship|intern|trainee)\b", title + " " + contract, re.I):
                continue
            details_url = value.get("ref") or base + "/" + str(identifier)
            details = client.get_json(details_url)
            sections = details.get("jobAd", {}).get("sections", {})
            description = " ".join(plain(s.get("text", "")) for s in sections.values() if isinstance(s, dict))
            location = details.get("location", value.get("location", {}))
            job_url = details.get("postingUrl") or "https://jobs.smartrecruiters.com/" + source["board"] + "/" + str(identifier)
            job = RawJob(title=title, company=source.get("company", source["board"]), location=", ".join(str(location[k]) for k in ("city", "region", "country") if location.get(k)), description=description, url=job_url, apply_url=details.get("applyUrl") or job_url, posted_at=details.get("releasedDate", ""), source=source["name"], contract_type=contract, work_arrangement="Remote" if location.get("remote") else "")
            job.metadata["identifier"] = identifier
            result.jobs.append(_api_metadata(job, details_url))
        offset += len(values)
        if offset >= int(data["totalFound"]):
            break


def workday(source, client, result):
    host = source["host"].rstrip("/")
    base = f"{host}/wday/cxs/{source['tenant']}/{source['board']}"
    seen = set()
    for query in source.get("queries", ["stage C++", "internship C++"]):
        offset = 0
        page_seen = set()
        while True:
            payload = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": query}
            data = client.post_json(base + "/jobs", payload)
            values = data.get("jobPostings")
            if not isinstance(values, list) or "total" not in data:
                raise SourceError("Workday response lacks jobPostings/total")
            result.pages += 1
            if not values:
                if offset < int(data["total"]):
                    raise SourceError("Workday pagination ended before advertised total")
                break
            for value in values:
                path = value.get("externalPath", "")
                if not path or path in page_seen:
                    raise SourceError("Workday repeated/missing externalPath")
                page_seen.add(path)
                if path in seen:
                    continue
                seen.add(path)
                details = client.get_json(base + path)
                detail = details.get("jobPostingInfo", {})
                if not detail:
                    raise SourceError("Workday details missing jobPostingInfo")
                job_url = detail.get("externalUrl") or host + "/en-US/" + source["board"] + path
                job = RawJob(title=plain(detail.get("title") or value.get("title")), company=source.get("company", source["tenant"]), location=plain(detail.get("location") or value.get("locationsText")), description=plain(detail.get("jobDescription")), url=job_url, apply_url=job_url, posted_at=detail.get("startDate", ""), source=source["name"], contract_type=plain(detail.get("jobTimeType")), work_arrangement=plain(detail.get("remoteType")))
                job.metadata["identifier"] = detail.get("jobReqId", "")
                result.jobs.append(_api_metadata(job, base + path))
            offset += len(values)
            if offset >= int(data["total"]):
                break


def jobpage(source, client, result):
    response = client.fetch(source["url"])
    result.pages += 1
    values = list(jobposting_objects(response.text))
    if not values:
        raise SourceError("No structured JobPosting found; page may require JavaScript or have closed")
    for value in values:
        job = from_jsonld(value, response.url, source)
        _application_link(job, response)
        if response.headers.get("x-snapshot-at"):
            job.metadata["snapshot_at"] = response.headers["x-snapshot-at"]
        result.jobs.append(job)


def html_search(source, client, result):
    seen_pages = set()
    seen_jobs = set()
    resolved_links = {}
    link_pattern = re.compile(source.get("job_link_pattern", r"/(?:jobs|job|emploi|offres|careers)/[^?#]+"), re.I)
    queries = source.get("queries", ["stage C++ Paris", "C++ internship Paris"]) if "{query}" in source["url"] else [""]
    for query in queries:
        page_url = source["url"].replace("{query}", quote(query, safe=""))
        query_jobs = set()
        advertised_total = 0
        while page_url:
            if page_url in seen_pages:
                raise SourceError("HTML search repeated a pagination URL")
            seen_pages.add(page_url)
            response = client.fetch(page_url)
            text = response.text
            result.pages += 1
            parsed = PageParser(text)
            visible = " ".join(parsed.visible_text)
            structured = list(jobposting_objects(text))
            for value in structured:
                job = from_jsonld(value, response.url, source)
                query_jobs.add(job.url)
                if job.url not in seen_jobs:
                    seen_jobs.add(job.url)
                    result.jobs.append(job)
            links = [urljoin(response.url, attrs["href"]) for attrs, _ in parsed.links if attrs.get("href") and link_pattern.search(urljoin(response.url, attrs["href"]))]
            for url in dict.fromkeys(links):
                if url in seen_jobs:
                    query_jobs.update(resolved_links.get(url, {url}))
                    continue
                seen_jobs.add(url)
                sub = SourceResult(source["name"], url)
                jobpage({**source, "url": url}, client, sub)
                result.jobs.extend(sub.jobs)
                resolved_links[url] = {job.url for job in sub.jobs}
                query_jobs.update(resolved_links[url])
            if not structured and not links and not re.search(source.get("empty_pattern", r"\bno (?:matching )?(?:jobs|results)\b|\baucun(?:e)? (?:offre|résultat)\w*\b|\b0 (?:offre|job)\w*\b"), visible, re.I):
                raise SourceError("No jobs or explicit empty-state found; unsupported, blocked or JavaScript search")
            next_links = []
            numbered = []
            current_number = 1
            current_query = parse_qs(urlparse(response.url).query)
            for key in ("page", "p", "pageNumber"):
                if current_query.get(key, [""])[0].isdigit():
                    current_number = int(current_query[key][0])
            for attrs, label in parsed.links:
                if attrs.get("aria-current") == "page" and label.strip().isdigit():
                    current_number = int(label.strip())
            for attrs, label in parsed.links:
                target = urljoin(response.url, attrs.get("href", ""))
                labels = [label, attrs.get("aria-label", ""), attrs.get("title", "")]
                if attrs.get("href") and ("next" in attrs.get("rel", "").split() or any(re.fullmatch(r"\s*(?:(?:next|suivant(?:e)?)(?: page)?|page suivante|›|»|>)\s*", v, re.I) for v in labels)):
                    next_links.append(target)
                if attrs.get("href") and label.strip().isdigit() and urlparse(target).path == urlparse(response.url).path:
                    params = parse_qs(urlparse(target).query)
                    if any(params.get(key, [""])[0] == label.strip() for key in ("page", "p", "pageNumber")):
                        numbered.append((int(label.strip()), target))
            if not next_links:
                higher = [(number, url) for number, url in numbered if number > current_number]
                if higher:
                    next_number, next_url = min(higher)
                    if next_number != current_number + 1:
                        raise SourceError("Numbered pagination skips an intermediate page")
                    next_links = [next_url]
            if len(set(next_links)) > 1:
                raise SourceError("Ambiguous next-page links")
            counters = re.search(r"\b(?:page)\s*(\d+)\s*(?:of|sur|/)\s*(\d+)", visible, re.I)
            if not next_links and counters and int(counters[1]) < int(counters[2]):
                raise SourceError("Page counter advertises more pages but no next link could be identified")
            if not next_links:
                for attrs, label in parsed.controls:
                    if "disabled" in attrs or attrs.get("aria-disabled") == "true" or "hidden" in attrs:
                        continue
                    labels = [label, attrs.get("aria-label", ""), attrs.get("title", "")]
                    if any(re.fullmatch(r"\s*(?:(?:load|show|see) more(?: (?:jobs|results|offers))?|(?:voir|afficher|charger) plus(?: d['’](?:offres|emplois))?|next(?: page)?|suivant(?:e)?|page suivante|›|»|>)\s*", value, re.I) for value in labels):
                        raise SourceError("JavaScript pagination control advertises more results without a usable next link")
            totals = re.findall(r"\b([0-9]+(?:[ ,.\u00a0][0-9]{3})*)\s+(?:jobs?|job openings?|offers?|offres?(?: d['’]emploi)?|r[ée]sultats?|results?)\b", visible, re.I)
            if totals:
                advertised_total = max(advertised_total, *(int(re.sub(r"[^0-9]", "", value)) for value in totals))
            if not next_links and advertised_total > len(query_jobs):
                raise SourceError(f"Search advertises {advertised_total} jobs but only {len(query_jobs)} distinct postings were collected; pagination is incomplete")
            page_url = next_links[0] if next_links else None


ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters, "workday": workday, "jobpage": jobpage, "html_search": html_search}


def collect_source(source, client):
    result = SourceResult(source["name"], source.get("url") or source.get("host") or source.get("board", ""))
    try:
        adapter = ADAPTERS.get(source.get("kind"))
        if not adapter:
            raise SourceError(f"Unsupported source kind {source.get('kind')!r}")
        adapter(source, client, result)
        for job in result.jobs:
            enrich_job(job)
        result.complete = True
        LOG.info("Source %s complete: %s jobs, %s pages", result.name, len(result.jobs), result.pages)
    except Exception as exc:
        for job in result.jobs:
            enrich_job(job)
        result.error = f"{type(exc).__name__}: {exc}"
        LOG.error("Source %s incomplete after %s pages: %s", result.name, result.pages, result.error)
    return result


def collect_sources(config, http):
    scope = config.get("search", {}).get("scope", "paris")
    locations = {"paris": "Paris, France", "ile-de-france": "Île-de-France, France"}
    if scope not in locations:
        raise ValueError("search.scope must be 'paris' or 'ile-de-france'")
    location = quote(locations[scope], safe="")
    sources = []
    for configured in config.get("sources", []):
        if configured.get("enabled", True):
            source = configured.copy()
            if source.get("url"):
                source["url"] = source["url"].replace("{location}", location)
            sources.append(source)
    workers = int(config.get("network", {}).get("source_workers", 4))
    if workers < 1:
        raise ValueError("network.source_workers must be at least 1")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda source: collect_source(source, http), sources))
