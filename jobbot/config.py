"""Configuration loading: YAML file + CLI overrides."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLE_CONFIG = PACKAGE_DIR.parent / "config.example.yaml"


@dataclass
class SearchConfig:
    keywords: list[str] = field(default_factory=lambda: ["QA Engineer", "Data Analyst", "Automation Engineer"])
    location: str = "United States"
    max_age_days: int = 14
    max_pages: int = 3
    fetch_descriptions: bool = True
    max_details: int = 60


@dataclass
class BrowserConfig:
    headed: bool = False
    profile_dir: str = ".browser-profile"
    delay_seconds: tuple[float, float] = (1.5, 4.0)
    timeout_seconds: float = 30.0


@dataclass
class QAConfig:
    include_keywords: list[str] = field(default_factory=lambda: ["qa", "quality", "test", "sdet", "data analyst", "automation", "analytics"])
    exclude_keywords: list[str] = field(default_factory=lambda: ["senior", "staff", "principal", "director", "manager", "lead", "intern", "clearance"])
    remote_ok: bool = True
    locations: list[str] = field(default_factory=lambda: ["United States", "US", "USA", ", ", " Area"])
    max_age_days: int = 30
    min_description_chars: int = 200
    salary_bounds_usd_year: tuple[float, float] = (20_000, 500_000)
    blocked_companies: list[str] = field(default_factory=list)
    agency_patterns: list[str] = field(default_factory=lambda: ["staffing", "recruit", "talent", r"consultants?$"])
    spam_patterns: list[str] = field(default_factory=lambda: [r"earn \$?\d+.*(per|a) (day|week)", r"no experience (needed|necessary|required)", r"unlimited (earning|income)", r"be your own boss"])


@dataclass
class Config:
    search: SearchConfig = field(default_factory=SearchConfig)
    sources: dict[str, bool] = field(default_factory=lambda: {"linkedin": True, "indeed": True, "glassdoor": True})
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    qa: QAConfig = field(default_factory=QAConfig)
    db_path: str = "data/jobs.db"
    output_dir: str = "output"
    filename: str = "jobs-{date}.xlsx"
    project_dir: Path = field(default_factory=Path.cwd)

    @property
    def enabled_sources(self) -> list[str]:
        return [name for name, on in self.sources.items() if on]

    def resolve(self, p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.project_dir / p


def _pick(d: dict[str, Any], cls, **overrides):
    """Build dataclass `cls` from dict keys it knows about, ignoring unknown ones."""
    known = {f.name for f in cls.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in d.items() if k in known}
    kwargs.update(overrides)
    for k in ("delay_seconds", "salary_bounds_usd_year"):
        if k in kwargs and isinstance(kwargs[k], list):
            kwargs[k] = tuple(kwargs[k])
    return cls(**kwargs)


def load_config(path: str | Path | None = None, project_dir: Path | None = None) -> Config:
    project_dir = project_dir or Path.cwd()
    path = Path(path) if path else project_dir / "config.yaml"
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
    elif EXAMPLE_CONFIG.exists():
        raw = yaml.safe_load(EXAMPLE_CONFIG.read_text()) or {}

    sources_raw = raw.get("sources", {}) or {}
    sources = {name: bool((sources_raw.get(name) or {}).get("enabled", True)) for name in ("linkedin", "indeed", "glassdoor")}
    storage = raw.get("storage", {}) or {}
    export = raw.get("export", {}) or {}
    return Config(
        search=_pick(raw.get("search", {}) or {}, SearchConfig),
        sources=sources,
        browser=_pick(raw.get("browser", {}) or {}, BrowserConfig),
        qa=_pick(raw.get("qa", {}) or {}, QAConfig),
        db_path=storage.get("db_path", "data/jobs.db"),
        output_dir=export.get("output_dir", "output"),
        filename=export.get("filename", "jobs-{date}.xlsx"),
        project_dir=project_dir,
    )


def write_example(dest: Path, force: bool = False) -> bool:
    if dest.exists() and not force:
        return False
    shutil.copyfile(EXAMPLE_CONFIG, dest)
    return True
