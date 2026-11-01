"""Data exchanged by collection, matching, verification and reporting."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawJob:
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    url: str = ""
    apply_url: str = ""
    start_date: str = ""
    duration: str = ""
    posted_at: str = ""
    source: str = ""
    contract_type: str = ""
    work_arrangement: str = ""
    status: str = "unverified"
    verification_evidence: str = ""
    verified_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceResult:
    name: str
    url: str
    jobs: list[RawJob] = field(default_factory=list)
    pages: int = 0
    complete: bool = False
    error: str = ""


class SourceError(RuntimeError):
    """A source could not be collected completely; never equate it with zero jobs."""
