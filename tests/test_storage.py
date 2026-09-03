from datetime import date, datetime, timezone

from jobbot.models import Job, RunSummary
from jobbot.qa.pipeline import run_qa
from jobbot.config import QAConfig
from jobbot.storage import Storage


def test_mark_seen_tracks_new_across_runs(tmp_path):
    db = tmp_path / "jobs.db"
    j1 = Job(source="indeed", source_id="a", title="QA Engineer", company="Acme", location="Remote", url="https://www.indeed.com/viewjob?jk=a", posted_at=date(2026, 9, 1), salary_min=90000.0)
    with Storage(db) as s:
        assert s.mark_seen([j1]) == 1
        assert j1.is_new is True
    j1_again = Job(source="indeed", source_id="a", title="QA Engineer", company="Acme", location="Remote", url="https://www.indeed.com/viewjob?jk=a")
    j2 = Job(source="glassdoor", source_id="b", title="Data Analyst", company="Beta", url="https://www.glassdoor.com/x")
    with Storage(db) as s:
        assert s.mark_seen([j1_again, j2]) == 1
        assert j1_again.is_new is False and j2.is_new is True
        assert j1_again.first_seen == j1.first_seen
        assert s.count() == 2
        report = run_qa([j1_again, j2], QAConfig())
        s.record_qa(report.results)
        loaded = s.load_jobs()
        assert {j.source_id for j in loaded} == {"a", "b"}
        a = next(j for j in loaded if j.source_id == "a")
        assert a.posted_at == date(2026, 9, 1) and a.salary_min == 90000.0 and a.remote is True
        assert len(s.load_jobs(only_kept=True)) == 2


def test_run_log_roundtrip(tmp_path):
    with Storage(tmp_path / "jobs.db") as s:
        summary = RunSummary(run_id="r1", started_at=datetime.now(timezone.utc), scraped_by_source={"linkedin": 3}, kept_by_source={"linkedin": 2}, blocked_sources=["indeed"], rejection_counts={"duplicate": 1}, new_count=3)
        s.save_run(summary)
        got = s.last_run()
        assert got.run_id == "r1" and got.scraped == 3 and got.kept == 2 and got.blocked_sources == ["indeed"]
