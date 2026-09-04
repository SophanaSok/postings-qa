"""Configuration loading: YAML file + CLI overrides, plus comment-preserving write-back for the UI."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLE_CONFIG = PACKAGE_DIR.parent / "config.example.yaml"
SOURCE_NAMES = ("linkedin", "indeed", "glassdoor")


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
    sources: dict[str, bool] = field(default_factory=lambda: {name: True for name in SOURCE_NAMES})
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


def config_path(path: str | Path | None = None, project_dir: Path | None = None) -> Path:
    """The config file a run would read: the given path, else <project_dir>/config.yaml."""
    project_dir = project_dir or Path.cwd()
    return Path(path) if path else project_dir / "config.yaml"


def config_from_raw(raw: dict[str, Any], project_dir: Path | None = None) -> Config:
    """Build a Config from a parsed YAML mapping (the same rules as load_config)."""
    raw = raw or {}
    sources_raw = raw.get("sources", {}) or {}
    sources = {name: bool((sources_raw.get(name) or {}).get("enabled", True)) for name in SOURCE_NAMES}
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
        project_dir=project_dir or Path.cwd(),
    )


def load_config(path: str | Path | None = None, project_dir: Path | None = None) -> Config:
    project_dir = project_dir or Path.cwd()
    path = config_path(path, project_dir)
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
    elif EXAMPLE_CONFIG.exists():
        raw = yaml.safe_load(EXAMPLE_CONFIG.read_text()) or {}
    return config_from_raw(raw, project_dir)


def write_example(dest: Path, force: bool = False) -> bool:
    if dest.exists() and not force:
        return False
    shutil.copyfile(EXAMPLE_CONFIG, dest)
    return True


# -- round-trip editing (used by the web UI; needs the `ui` extra for ruamel.yaml) ---------------

def _yaml_rt():
    from ruamel.yaml import YAML

    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)  # matches config.example.yaml's `key:\n    - item` layout
    return y


def load_raw(path: str | Path | None = None, project_dir: Path | None = None):
    """Load config.yaml as a comment-preserving mapping (falls back to the example file)."""
    path = config_path(path, project_dir)
    src = path if path.exists() else EXAMPLE_CONFIG
    return _yaml_rt().load(src.read_text()) or {}


def save_raw(raw, path: str | Path) -> Path:
    """Write a mapping from load_raw back to disk, keeping comments and key order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        _yaml_rt().dump(raw, fh)
    return path


def flow_list(values) -> Any:
    """A list that dumps inline (`[a, b]`) so short pairs like delay_seconds stay on one line."""
    from ruamel.yaml.comments import CommentedSeq

    seq = CommentedSeq(list(values))
    seq.fa.set_flow_style()
    return seq
