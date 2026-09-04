"""`pqa demo` seeds a history that exercises every QA check and builds a workbook, with no network."""

from postingsqa.cli import main
from postingsqa.config import QAConfig, load_config
from postingsqa.demo import synthetic_jobs
from postingsqa.qa.checks import CHECKS
from postingsqa.qa.pipeline import run_qa
from postingsqa.storage import Storage


def test_synthetic_jobs_trip_every_check():
    jobs = synthetic_jobs(120, seed=1)
    assert len(jobs) > 120 and len({j.source for j in jobs}) == 8
    report = run_qa(jobs, QAConfig())
    assert len(report.kept) > 40 and report.rejected
    for name in ["duplicate"] + [n for n, _ in CHECKS]:
        assert report.rejection_counts.get(name, 0) >= 1, f"check {name} never fired"
    assert [(j.id, j.title, j.salary_min) for j in synthetic_jobs(20, seed=7)] == [(j.id, j.title, j.salary_min) for j in synthetic_jobs(20, seed=7)]  # deterministic


def test_demo_command_seeds_history_and_workbook(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["demo", "--jobs", "60"]) == 0
    out = capsys.readouterr().out
    assert "synthetic data" in out and "workbook:" in out
    cfg = load_config(None, tmp_path)
    with Storage(cfg.resolve(cfg.db_path)) as store:
        runs = store.list_runs()
        assert len(runs) == 2 and runs[0].run_id.startswith("demo-") and runs[0].blocked_sources == ["glassdoor"]
        assert 0 < runs[0].new_count < store.count()  # half were "seen" by the previous run
    assert list((tmp_path / "output").glob("*.xlsx"))
    # --reset starts over: one fresh pair of runs, not four
    assert main(["demo", "--jobs", "30", "--reset"]) == 0
    with Storage(cfg.resolve(cfg.db_path)) as store:
        assert len(store.list_runs()) == 2
