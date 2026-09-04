"""Read-only data access for the web UI. Nothing in here scrapes or writes."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from postingsqa.config import Config, config_from_raw, config_path, load_config
from postingsqa.export.charts import CATEGORICAL, NEUTRAL, PRIMARY, SOURCE_COLOR, STATUS_BAD, STATUS_GOOD
from postingsqa.export.excel import SALARY_BUCKETS
from postingsqa.models import RunSummary
from postingsqa.qa.checks import annualize
from postingsqa.qa.pipeline import QAReport, run_qa
from postingsqa.storage import Storage

# -- palette (shared with the Excel dashboard; openpyxl wants bare hex, Altair wants '#') ----------

def hexcolor(c: str) -> str:
    return f"#{c}"


SOURCE_COLORS = {k: hexcolor(v) for k, v in SOURCE_COLOR.items()}
STATUS_COLORS = {"kept": hexcolor(STATUS_GOOD), "rejected": hexcolor(STATUS_BAD)}
PRIMARY_COLOR = hexcolor(PRIMARY)
NEUTRAL_COLOR = hexcolor(NEUTRAL)
CATEGORICAL_COLORS = [hexcolor(c) for c in CATEGORICAL]


# -- project / config -------------------------------------------------------------------------

def project_dir() -> Path:
    return Path(os.environ.get("PQA_PROJECT_DIR") or Path.cwd()).resolve()


def cfg_path() -> Path:
    return config_path(os.environ.get("PQA_CONFIG"), project_dir())


def get_config() -> Config:
    """Always re-read: it is a tiny file and the Settings page writes it."""
    return load_config(cfg_path(), project_dir())


def db_path(cfg: Config | None = None) -> Path:
    cfg = cfg or get_config()
    return cfg.resolve(cfg.db_path)


def db_stamp(path: Path) -> int:
    """Cache key that changes whenever a run writes the database."""
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0


def since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# -- jobs -------------------------------------------------------------------------------------

def _salary_year_mid(row) -> float | None:
    vals = [v for v in (row["salary_min"], row["salary_max"]) if pd.notna(v)]
    if not vals:
        return None
    cur = row["salary_currency"]
    if isinstance(cur, str) and cur.upper() != "USD":
        return None
    period = row["salary_period"]
    return annualize(sum(vals) / len(vals), period if isinstance(period, str) else None)


@st.cache_data(show_spinner=False)
def load_jobs_df(path: str, stamp: int, days: int) -> pd.DataFrame:
    """Every job row seen in the last `days` days, including qa_status / qa_reason."""
    if not Path(path).exists():
        return pd.DataFrame()
    with Storage(path) as store:
        df = pd.read_sql_query(
            "SELECT * FROM jobs WHERE last_seen >= ? ORDER BY last_seen DESC, posted_at DESC",
            store.conn,
            params=(since_iso(days),),
        )
    if df.empty:
        return df
    for col in ("salary_min", "salary_max"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype("string")  # NULL -> <NA>, which renders blank instead of "None"
    df["remote"] = df["remote"].map({1: True, 0: False, 1.0: True, 0.0: False})
    df["salary_is_estimate"] = df["salary_is_estimate"].fillna(0).astype(bool)
    df["posted_at"] = pd.to_datetime(df["posted_at"], errors="coerce").dt.date
    for col in ("first_seen", "last_seen"):
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    df["is_new"] = df["first_seen"] == df["last_seen"]
    df["salary_year_mid"] = df.apply(_salary_year_mid, axis=1)
    df["qa_status"] = df["qa_status"].fillna("unknown")
    return df


def jobs_df(days: int, cfg: Config | None = None) -> pd.DataFrame:
    path = db_path(cfg)
    return load_jobs_df(str(path), db_stamp(path), days)


# -- runs -------------------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _load_runs(path: str, stamp: int, limit: int) -> list[RunSummary]:
    if not Path(path).exists():
        return []
    with Storage(path) as store:
        return store.list_runs(limit)


def runs(limit: int = 50, cfg: Config | None = None) -> list[RunSummary]:
    path = db_path(cfg)
    return _load_runs(str(path), db_stamp(path), limit)


def last_run(cfg: Config | None = None) -> RunSummary | None:
    r = runs(1, cfg)
    return r[0] if r else None


def job_count(cfg: Config | None = None) -> int:
    path = db_path(cfg)
    if not path.exists():
        return 0
    with Storage(path) as store:
        return store.count()


def runs_df(items: list[RunSummary]) -> pd.DataFrame:
    rows = []
    for r in items:
        dur = (r.finished_at - r.started_at).total_seconds() if r.finished_at else None
        rows.append({
            "run": r.run_id,
            "started": r.started_at.astimezone(),
            "duration_s": round(dur) if dur is not None else None,
            "scraped": r.scraped,
            "kept": r.kept,
            "rejected": r.rejected,
            "new": r.new_count,
            "blocked": ", ".join(r.blocked_sources),
            "errors": "; ".join(f"{k}: {v}" for k, v in r.errors.items()),
        })
    return pd.DataFrame(rows)


# -- aggregations for charts ------------------------------------------------------------------

def by_source_status(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby(["source", "qa_status"]).size().reset_index(name="jobs")


def top_companies(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    kept = df[df["qa_status"] == "kept"]
    return kept["company"].dropna().value_counts().head(n).rename_axis("company").reset_index(name="jobs")


def postings_per_day(df: pd.DataFrame, days: int) -> pd.DataFrame:
    kept = df[df["qa_status"] == "kept"].dropna(subset=["posted_at"])
    if kept.empty:
        return pd.DataFrame(columns=["day", "jobs"])
    end = datetime.now().date()
    start = max(kept["posted_at"].min(), end - timedelta(days=days - 1))
    idx = pd.date_range(start, end, freq="D").date
    counts = kept["posted_at"].value_counts()
    return pd.DataFrame({"day": idx, "jobs": [int(counts.get(d, 0)) for d in idx]})


def remote_split(df: pd.DataFrame) -> pd.DataFrame:
    kept = df[df["qa_status"] == "kept"]
    label = kept["remote"].map({True: "Remote", False: "On-site / hybrid"}).fillna("Unknown")
    return label.value_counts().rename_axis("kind").reset_index(name="jobs")


def salary_buckets(df: pd.DataFrame) -> pd.DataFrame:
    kept = df[df["qa_status"] == "kept"]
    mids = kept["salary_year_mid"].dropna()
    rows = [(label, int(((mids >= lo) & (mids < hi)).sum())) for label, lo, hi in SALARY_BUCKETS]
    return pd.DataFrame(rows, columns=["bucket", "jobs"])


def rejections_df(summary: RunSummary | None) -> pd.DataFrame:
    if not summary or not summary.rejection_counts:
        return pd.DataFrame(columns=["check", "jobs"])
    items = sorted(summary.rejection_counts.items(), key=lambda kv: -kv[1])
    return pd.DataFrame(items, columns=["check", "jobs"])


def latest_workbook(cfg: Config) -> Path | None:
    out_dir = cfg.resolve(cfg.output_dir)
    files = sorted(out_dir.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True) if out_dir.exists() else []
    return files[0] if files else None


# -- QA preview (re-run the checks on stored jobs with a candidate QA config) -----------------

def preview_qa(qa_raw: dict, days: int, cfg: Config | None = None) -> QAReport | None:
    path = db_path(cfg)
    if not path.exists():
        return None
    qa_cfg = config_from_raw({"qa": qa_raw}).qa
    with Storage(path) as store:
        jobs = store.load_jobs(since_run=since_iso(days))
    return run_qa(jobs, qa_cfg)
