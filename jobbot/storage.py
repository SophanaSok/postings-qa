"""SQLite persistence: job history across runs and a run log."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jobbot.models import Job, RunSummary

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT,
    title TEXT, company TEXT, location TEXT, url TEXT,
    remote INTEGER,
    posted_at TEXT, posted_raw TEXT,
    salary_min REAL, salary_max REAL, salary_currency TEXT, salary_period TEXT, salary_raw TEXT,
    salary_is_estimate INTEGER DEFAULT 0,
    employment_type TEXT, seniority TEXT, description TEXT, search_query TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    qa_status TEXT,
    qa_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary_json TEXT NOT NULL
);
"""

JOB_COLUMNS = [
    "id", "source", "source_id", "title", "company", "location", "url", "remote",
    "posted_at", "posted_raw", "salary_min", "salary_max", "salary_currency", "salary_period",
    "salary_raw", "salary_is_estimate", "employment_type", "seniority", "description", "search_query",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- jobs -------------------------------------------------------------

    def mark_seen(self, jobs: list[Job]) -> int:
        """Upsert jobs; set is_new/first_seen on each. Returns count of newly seen jobs."""
        now = _now()
        new = 0
        for job in jobs:
            row = self.conn.execute("SELECT first_seen FROM jobs WHERE id = ?", (job.id,)).fetchone()
            if row:
                job.is_new = False
                job.first_seen = datetime.fromisoformat(row["first_seen"])
                first_seen = row["first_seen"]
            else:
                job.is_new = True
                job.first_seen = datetime.fromisoformat(now)
                first_seen = now
                new += 1
            d = job.to_dict()
            values = [d.get(c) for c in JOB_COLUMNS]
            values[JOB_COLUMNS.index("remote")] = None if job.remote is None else int(job.remote)
            values[JOB_COLUMNS.index("salary_is_estimate")] = int(job.salary_is_estimate)
            placeholders = ", ".join("?" for _ in JOB_COLUMNS)
            # keep previously known values when a re-scrape lacks them (e.g. no detail fetch this run)
            updates = ", ".join(f"{c}=COALESCE(excluded.{c}, jobs.{c})" for c in JOB_COLUMNS if c != "id")
            self.conn.execute(
                f"INSERT INTO jobs ({', '.join(JOB_COLUMNS)}, first_seen, last_seen) "
                f"VALUES ({placeholders}, ?, ?) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}, last_seen=excluded.last_seen",
                [*values, first_seen, now],
            )
        self.conn.commit()
        return new

    def record_qa(self, results) -> None:
        self.conn.executemany(
            "UPDATE jobs SET qa_status = ?, qa_reason = ? WHERE id = ?",
            [("kept" if r.passed else "rejected", r.reason or None, r.job.id) for r in results],
        )
        self.conn.commit()

    def load_jobs(self, since_run: str | None = None, only_kept: bool = False, limit: int | None = None) -> list[Job]:
        sql = "SELECT * FROM jobs"
        where = []
        params: list = []
        if only_kept:
            where.append("qa_status = 'kept'")
        if since_run:
            where.append("last_seen >= ?")
            params.append(since_run)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_seen DESC, posted_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        jobs = []
        for row in self.conn.execute(sql, params):
            d = dict(row)
            d["remote"] = None if d["remote"] is None else bool(d["remote"])
            d["salary_is_estimate"] = bool(d["salary_is_estimate"])
            d["is_new"] = d["first_seen"] == d["last_seen"]
            d["scraped_at"] = d["last_seen"]
            jobs.append(Job.from_dict(d))
        return jobs

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # -- runs -------------------------------------------------------------

    def save_run(self, summary: RunSummary) -> None:
        payload = {
            "scraped_by_source": summary.scraped_by_source,
            "kept_by_source": summary.kept_by_source,
            "blocked_sources": summary.blocked_sources,
            "errors": summary.errors,
            "rejection_counts": summary.rejection_counts,
            "new_count": summary.new_count,
        }
        self.conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, started_at, finished_at, summary_json) VALUES (?, ?, ?, ?)",
            (
                summary.run_id,
                summary.started_at.isoformat(),
                summary.finished_at.isoformat() if summary.finished_at else None,
                json.dumps(payload),
            ),
        )
        self.conn.commit()

    @staticmethod
    def _run_from_row(row) -> RunSummary:
        payload = json.loads(row["summary_json"])
        return RunSummary(
            run_id=row["run_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            **payload,
        )

    def list_runs(self, limit: int = 50) -> list[RunSummary]:
        """Most recent runs first."""
        rows = self.conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [self._run_from_row(r) for r in rows]

    def last_run(self) -> RunSummary | None:
        runs = self.list_runs(limit=1)
        return runs[0] if runs else None
