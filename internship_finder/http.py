"""Small HTTP client with bounded retry/backoff and auditable offline snapshots."""
import email.utils
import json
import logging
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .models import SourceError

LOG = logging.getLogger(__name__)


@dataclass
class HttpResponse:
    url: str
    status: int
    text: str
    headers: dict[str, str]


class HttpClient:
    def __init__(self, timeout=20, retries=3, snapshot_dir=None):
        self.timeout = timeout
        self.retries = retries
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        self.manifest = None
        if self.snapshot_dir:
            self.manifest = json.loads((self.snapshot_dir / "manifest.json").read_text())

    def fetch(self, url, payload=None):
        # A snapshot run never silently substitutes a fresh or cached response.
        if self.manifest is not None:
            key = url if payload is None else url + " " + json.dumps(payload, sort_keys=True)
            record = self.manifest.get(key)
            if not record:
                raise SourceError(f"No snapshot for {key}")
            path = (self.snapshot_dir / record["file"]).resolve()
            if not path.is_relative_to(self.snapshot_dir.resolve()):
                raise SourceError("Snapshot path escapes snapshot directory")
            LOG.info("Snapshot %s captured=%s provenance=%s", url, record.get("fetched_at"), record.get("provenance"))
            return HttpResponse(record.get("final_url", url), record.get("status", 200), path.read_text(), {"x-snapshot-at": record.get("fetched_at", ""), "x-snapshot-provenance": record.get("provenance", "")})
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"User-Agent": "ParisCppInternships/1.0 (public job availability research)", "Accept": "application/json,text/html;q=0.9,*/*;q=0.5"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers)
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return HttpResponse(response.url, response.status, response.read().decode(charset, errors="replace"), dict(response.headers.items()))
            except urllib.error.HTTPError as exc:
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                exc.close()
                retryable = exc.code in {408, 425, 429, 500, 502, 503, 504}
                if not retryable or attempt == self.retries:
                    raise SourceError(f"HTTP {exc.code}: {url}") from exc
                try:
                    delay = float(retry_after)
                except ValueError:
                    try:
                        delay = max(0, email.utils.parsedate_to_datetime(retry_after).timestamp() - time.time())
                    except (ValueError, TypeError):
                        delay = 2 ** attempt
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    raise SourceError(f"Network failure: {url}: {exc}") from exc
                delay = 2 ** attempt
            delay = min(60, delay) + random.uniform(0, .25)
            LOG.warning("Retry %s/%s %s in %.2fs", attempt + 1, self.retries, url, delay)
            time.sleep(delay)
        raise AssertionError("Unreachable")

    def get(self, url):
        return self.fetch(url).text

    def get_json(self, url):
        try:
            return json.loads(self.get(url))
        except json.JSONDecodeError as exc:
            raise SourceError(f"Invalid JSON from {url}: {exc}") from exc

    def post_json(self, url, payload):
        try:
            return json.loads(self.fetch(url, payload).text)
        except json.JSONDecodeError as exc:
            raise SourceError(f"Invalid JSON from {url}: {exc}") from exc
