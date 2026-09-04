from datetime import date, timedelta

from postingsqa.config import QAConfig
from postingsqa.models import Job
from postingsqa.qa import checks
from postingsqa.qa.pipeline import run_qa


def mk(**kw) -> Job:
    base = dict(
        source="linkedin",
        source_id="1",
        title="QA Engineer",
        company="Acme Corp",
        location="Austin, TX",
        url="https://www.linkedin.com/jobs/view/1",
        posted_at=date.today() - timedelta(days=2),
        description="x" * 300,
    )
    base.update(kw)
    return Job(**base)


def test_required_fields():
    assert checks.required_fields(mk(title=""), QAConfig()) == "missing title"
    assert checks.required_fields(mk(), QAConfig()) is None


def test_url_host_must_match_source():
    assert checks.url_valid(mk(url="https://evil.example/x"), QAConfig()) is not None
    assert checks.url_valid(mk(url="not a url"), QAConfig()) == "malformed url"
    assert checks.url_valid(mk(), QAConfig()) is None


def test_title_relevance():
    cfg = QAConfig()
    assert checks.title_relevance(mk(title="Senior QA Engineer"), cfg).startswith("title contains excluded")
    assert checks.title_relevance(mk(title="Forklift Operator"), cfg) == "title matches no include keyword"
    assert checks.title_relevance(mk(title="Software Test Engineer (Automation)"), cfg) is None
    # "lead" must match whole words only
    assert checks.title_relevance(mk(title="QA Engineer - Leading Fintech"), cfg) is None


def test_location_match_and_remote():
    cfg = QAConfig(locations=["United States", ", "])
    assert checks.location_match(mk(location="Berlin"), cfg) is not None
    assert checks.location_match(mk(location="Remote - Berlin"), cfg) is None  # remote inferred
    assert checks.location_match(mk(location=""), cfg) == ("flag", "location unknown")
    assert checks.location_match(mk(location="Austin, TX"), cfg) is None


def test_posting_age():
    cfg = QAConfig(max_age_days=30)
    assert checks.posting_age(mk(posted_at=date.today() - timedelta(days=45)), cfg) is not None
    assert checks.posting_age(mk(posted_at=None), cfg) == ("flag", "posted date unknown")
    assert checks.posting_age(mk(), cfg) is None


def test_salary_sanity():
    cfg = QAConfig()
    assert checks.salary_sanity(mk(salary_min=90000, salary_max=80000), cfg) is not None
    assert checks.salary_sanity(mk(salary_min=5, salary_max=8, salary_period="hour"), cfg) is not None
    assert checks.salary_sanity(mk(salary_min=40, salary_max=60, salary_period="hour"), cfg) is None
    assert checks.salary_sanity(mk(salary_min=90000, salary_is_estimate=True), cfg)[0] == "flag"
    assert checks.salary_sanity(mk(), cfg) is None


def test_description_quality():
    cfg = QAConfig()
    assert checks.description_quality(mk(description="short"), cfg).startswith("description too short")
    spam = "Great job! " * 30 + "No experience needed, earn $500 per day"
    assert "spam" in checks.description_quality(mk(description=spam), cfg)
    assert checks.description_quality(mk(description=None), cfg) == ("flag", "no description fetched")


def test_staffing_agency():
    cfg = QAConfig(blocked_companies=["Bad Co"])
    assert checks.staffing_agency(mk(company="Bad Co"), cfg) is not None
    assert checks.staffing_agency(mk(company="Global Staffing Partners"), cfg) is not None
    assert checks.staffing_agency(mk(company="Acme Corp"), cfg) is None


def test_pipeline_dedupes_and_counts():
    cfg = QAConfig()
    jobs = [
        mk(source_id="1"),
        mk(source_id="1"),  # duplicate id
        mk(source_id="2", url="https://www.linkedin.com/jobs/view/2"),  # duplicate title/company/location
        mk(source_id="3", url="https://www.linkedin.com/jobs/view/3", title="Senior QA Engineer"),
        mk(source_id="4", url="https://www.linkedin.com/jobs/view/4", title="Data Analyst", company="Beta Inc", description=None),
    ]
    report = run_qa(jobs, cfg)
    assert report.scraped == 5
    assert [j.source_id for j in report.kept] == ["1", "4"]
    assert report.rejection_counts["duplicate"] == 2
    assert report.rejection_counts["title_relevance"] == 1
    assert report.flag_counts["description_quality"] == 1
    assert report.per_source["linkedin"] == {"scraped": 5, "kept": 2, "rejected": 3}
    assert report.rejected[0].reason == "duplicate id within run"
