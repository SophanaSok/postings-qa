"""UI layer: run manager against stub commands, export_history, and Streamlit smoke tests."""

import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from postingsqa.cli import export_history
from postingsqa.config import EXAMPLE_CONFIG, load_config
from postingsqa.models import Job, RunSummary
from postingsqa.qa.pipeline import run_qa
from postingsqa.storage import Storage

pytest.importorskip("streamlit")
from postingsqa.ui.runner import RunManager  # noqa: E402


def _wait(runner: RunManager, seconds: float = 15) -> int:
    deadline = time.time() + seconds
    while runner.running() and time.time() < deadline:
        time.sleep(0.05)
    assert not runner.running(), "stub process did not exit"
    return runner.returncode


def test_run_manager_logs_and_exit_code(tmp_path):
    runner = RunManager(tmp_path)
    log = runner.start(["--version"])  # python -u -m postingsqa.cli --version: exits 0 quickly
    assert log.parent == tmp_path / "data" / "runs"
    assert _wait(runner) == 0
    text = log.read_text()
    assert text.startswith("$ ") and "pqa 0." in text
    assert runner.tail(1) == text.splitlines(keepends=True)[-1]
    assert runner.past_logs() == [log]


def test_run_manager_stop_and_single_run(tmp_path, monkeypatch):
    monkeypatch.setattr(RunManager, "MODULE", "timeit")
    runner = RunManager(tmp_path)
    runner.start(["-n", "1", "-r", "1", "import time; time.sleep(30)"])
    assert runner.running()
    with pytest.raises(RuntimeError):
        runner.start(["-n", "1", "-r", "1", "pass"])
    t0 = time.time()
    runner.stop(grace_seconds=5)
    assert time.time() - t0 < 5
    assert not runner.running() and runner.returncode is not None and runner.returncode != 0


def _seed_project(tmp_path: Path) -> Path:
    (tmp_path / "config.yaml").write_text(EXAMPLE_CONFIG.read_text())
    cfg = load_config(tmp_path / "config.yaml", tmp_path)
    jobs = [
        Job(source="indeed", source_id="a", title="QA Engineer", company="Acme", location="Remote",
            url="https://www.indeed.com/viewjob?jk=a", posted_at=date.today(), salary_min=90000.0, salary_max=110000.0,
            salary_period="year", description="We test things. " * 30),
        Job(source="linkedin", source_id="b", title="Senior Data Analyst", company="Beta", location="Austin, TX",
            url="https://www.linkedin.com/jobs/view/b", posted_at=date.today(), description="x" * 300),
    ]
    with Storage(cfg.resolve(cfg.db_path)) as store:
        summary = RunSummary(run_id="seed", started_at=datetime.now(timezone.utc))
        summary.new_count = store.mark_seen(jobs)
        report = run_qa(jobs, cfg.qa)
        store.record_qa(report.results)
        summary.scraped_by_source = {"indeed": 1, "linkedin": 1}
        summary.kept_by_source = {"indeed": 1}
        summary.rejection_counts = dict(report.rejection_counts)
        summary.finished_at = datetime.now(timezone.utc)
        store.save_run(summary)
    return tmp_path


def test_export_history_builds_workbook(tmp_path):
    _seed_project(tmp_path)
    cfg = load_config(tmp_path / "config.yaml", tmp_path)
    out, report, summary = export_history(cfg, days=7)
    assert out.exists() and out.suffix == ".xlsx"
    assert report.scraped == 2 and len(report.kept) == 1
    assert summary.run_id == "export-7d"
    with pytest.raises(LookupError):
        export_history(load_config(tmp_path / "config.yaml", tmp_path / "empty"), days=7)


def _app(view: str):
    from streamlit.testing.v1 import AppTest

    return AppTest.from_string(f"import streamlit as st\nfrom postingsqa.ui.views import {view}\n{view}.render()\n", default_timeout=60)


@pytest.mark.parametrize("view", ["dashboard", "jobs", "settings"])
def test_views_render_without_exceptions(tmp_path, monkeypatch, view):
    _seed_project(tmp_path)
    monkeypatch.setenv("PQA_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("PQA_CONFIG", raising=False)
    at = _app(view).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value.startswith({"dashboard": "Job Postings", "jobs": "Jobs", "settings": "Settings"}[view])
    if view == "dashboard":
        assert at.metric[0].value == "2"  # scraped in the seeded run
    if view == "jobs":
        assert len(at.dataframe) == 1


def test_settings_save_changes_config(tmp_path, monkeypatch):
    _seed_project(tmp_path)
    monkeypatch.setenv("PQA_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("PQA_CONFIG", raising=False)
    at = _app("settings").run()
    pages = [n for n in at.number_input if n.label.startswith("Result pages")][0]
    pages.set_value(1)
    [b for b in at.button if b.label == "Save search settings"][0].click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("search.max_pages" in s.value for s in at.success), [s.value for s in at.success]
    cfg = load_config(tmp_path / "config.yaml", tmp_path)
    assert cfg.search.max_pages == 1
    assert "# result pages per keyword per source" in (tmp_path / "config.yaml").read_text()
