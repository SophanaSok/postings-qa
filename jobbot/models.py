"""Core data model shared by scrapers, QA, storage and export."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timezone
from typing import Any

SOURCES = ("linkedin", "indeed", "glassdoor")

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize(text: str | None) -> str:
    """Lower-case, strip punctuation and collapse whitespace for fuzzy comparisons."""
    if not text:
        return ""
    return _WS.sub(" ", _NON_ALNUM.sub(" ", text.lower())).strip()


def make_id(source: str, source_id: str | None, url: str | None) -> str:
    key = f"{source}:{source_id}" if source_id else f"{source}:{(url or '').split('?')[0].rstrip('/')}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class Job:
    source: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    url: str | None = None
    source_id: str | None = None
    id: str = ""
    remote: bool | None = None
    posted_at: date | None = None
    posted_raw: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # "year" | "hour" | "month" | "day" | "week"
    salary_raw: str | None = None
    salary_is_estimate: bool = False
    employment_type: str | None = None
    seniority: str | None = None
    description: str | None = None
    search_query: str | None = None
    scraped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    first_seen: datetime | None = None
    is_new: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            self.id = make_id(self.source, self.source_id, self.url)
        if self.remote is None:
            self.remote = infer_remote(self.title, self.location)

    @property
    def dedupe_key(self) -> str:
        return "|".join((normalize(self.title), normalize(self.company), normalize(self.location)))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, (date, datetime)):
                d[k] = v.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in known}
        if isinstance(data.get("posted_at"), str):
            data["posted_at"] = date.fromisoformat(data["posted_at"][:10])
        for k in ("scraped_at", "first_seen"):
            if isinstance(data.get(k), str):
                data[k] = datetime.fromisoformat(data[k])
        return cls(**data)


_REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|anywhere)\b", re.I)


def infer_remote(title: str | None, location: str | None) -> bool | None:
    text = f"{title or ''} {location or ''}"
    if not text.strip():
        return None
    return bool(_REMOTE_RE.search(text))


@dataclass
class QAResult:
    job: Job
    passed: bool
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


@dataclass
class RunSummary:
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    scraped_by_source: dict[str, int] = field(default_factory=dict)
    kept_by_source: dict[str, int] = field(default_factory=dict)
    blocked_sources: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    rejection_counts: dict[str, int] = field(default_factory=dict)
    new_count: int = 0

    @property
    def scraped(self) -> int:
        return sum(self.scraped_by_source.values())

    @property
    def kept(self) -> int:
        return sum(self.kept_by_source.values())

    @property
    def rejected(self) -> int:
        return self.scraped - self.kept
