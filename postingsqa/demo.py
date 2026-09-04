"""Synthetic job history for demos and tests.

`synthetic_jobs()` produces a realistic, deterministic mix across every source that deliberately trips every
QA check at least once (duplicates, missing fields, wrong hosts, excluded titles, staffing agencies, foreign
locations, stale and future dates, absurd or inverted salaries, thin and spammy descriptions), so the
dashboards have something to show without any network access. Nothing here is a real posting.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from postingsqa.config import SOURCE_NAMES
from postingsqa.models import Job, RunSummary

URL_TEMPLATES = {
    "remotive": "https://remotive.com/remote-jobs/qa/{slug}-{id}",
    "greenhouse": "https://boards.greenhouse.io/{company}/jobs/{id}",
    "lever": "https://jobs.lever.co/{company}/{id}",
    "usajobs": "https://www.usajobs.gov/GetJob/ViewDetails/{id}",
    "adzuna": "https://www.adzuna.com/land/ad/{id}",
    "linkedin": "https://www.linkedin.com/jobs/view/{id}",
    "indeed": "https://www.indeed.com/viewjob?jk={id}",
    "glassdoor": "https://www.glassdoor.com/job-listing/{slug}-JV_KO{id}.htm",
}
# Weighted so the API sources dominate, matching the default configuration.
SOURCE_WEIGHTS = {"remotive": 5, "greenhouse": 7, "lever": 6, "usajobs": 4, "adzuna": 2, "linkedin": 3, "indeed": 2, "glassdoor": 1}

GOOD_TITLES = [
    "QA Engineer", "Software Quality Engineer", "SDET", "Test Automation Engineer", "QA Analyst",
    "Data Analyst", "Data Quality Analyst", "Analytics Engineer", "Automation Engineer", "Quality Assurance Engineer II",
    "Junior QA Tester", "Data Analyst, Product", "QA Automation Engineer (Playwright)", "Data Quality Engineer",
]
EXCLUDED_TITLES = ["Senior QA Engineer", "QA Manager", "Staff Data Analyst", "Director of Quality Engineering", "QA Intern", "Lead SDET"]
IRRELEVANT_TITLES = ["Forklift Operator", "Registered Nurse", "Line Cook", "Account Executive"]

COMPANIES = [
    "Northwind Analytics", "Bluefin Software", "Harbor Health Systems", "Crestline Robotics", "Meridian Payments",
    "Quillfeather Labs", "Summit Grid Energy", "Lantern Learning", "Copperleaf Logistics", "Orbital Media",
    "Tidewater Insurance", "Granite Peak Games", "Verdant Agritech", "Skylark Mobility", "Ironwood Fintech",
    "Halcyon Biotech", "Pinecrest Retail", "Silverline Telecom", "Foxglove Security", "Aurora Data Co",
]
AGENCIES = ["Apex Staffing Group", "TalentBridge Recruiting", "Nimbus Consultants", "PrimeHire Solutions Inc"]
FEDERAL = ["Department of the Treasury", "Centers for Disease Control and Prevention", "General Services Administration", "Department of Veterans Affairs"]

US_LOCATIONS = ["Remote", "Remote - US", "Austin, TX", "New York, NY", "Denver, CO", "Seattle, WA", "Chicago, IL",
                "Raleigh, NC", "Greater Boston Area", "Atlanta, GA", "Phoenix, AZ", "Washington, DC", "Portland, OR"]
# No commas on purpose: the default QA `locations` list accepts ", " as the "City, ST" pattern.
FOREIGN_LOCATIONS = ["Berlin (Germany)", "Toronto (Canada)", "Bangalore (India)", "London (UK)"]

PARAGRAPHS = [
    "You will own end-to-end quality for our {product} platform: designing test strategies, building automated regression suites, and partnering with engineers to shift defects left.",
    "Day to day you will write and maintain automated tests in {stack}, triage failures in CI, and turn recurring escapes into new checks.",
    "Our data team ships dashboards and models that the whole company relies on. You will validate pipelines, profile datasets for anomalies, and define data-quality SLAs.",
    "We are a {size}-person team that values clear writing, small pull requests, and blameless retrospectives.",
    "Requirements: 2+ years in a QA, SDET, or data-analysis role; SQL; Python; experience with {stack}; comfort reading logs and reproducing issues.",
    "Nice to have: Playwright or Cypress, dbt or Airflow, statistics fundamentals, and a habit of documenting what you find.",
    "Benefits include health, dental and vision coverage, a 401(k) match, flexible PTO, and a home-office stipend.",
    "The salary range shown reflects the base compensation for this role; final offers depend on experience and location.",
]
PRODUCTS = ["payments", "analytics", "learning", "logistics", "fleet", "claims", "clinical-trials", "ad-serving", "identity"]
STACKS = ["pytest and Playwright", "Selenium and Java", "Cypress and TypeScript", "Great Expectations and dbt", "Postman and k6"]

SPAM_DESCRIPTIONS = [
    "Earn $500 per day from home. No experience needed! Unlimited earning potential, be your own boss. Apply now and start tomorrow. " * 3,
    "No experience necessary. Work whenever you want and earn $2,000 a week with our proven system. Unlimited income. " * 3,
]


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def _description(rng: random.Random) -> str:
    paras = rng.sample(PARAGRAPHS, k=rng.randint(3, 5))
    return "\n\n".join(p.format(product=rng.choice(PRODUCTS), stack=rng.choice(STACKS), size=rng.choice([8, 12, 25, 40, 120])) for p in paras)


def _salary(rng: random.Random, location: str) -> dict:
    kind = rng.random()
    if kind < 0.35:
        return {}
    if kind < 0.45:
        lo = rng.choice([28, 35, 42, 55, 70])
        return {"salary_min": float(lo), "salary_max": float(lo + rng.choice([5, 10, 20])), "salary_currency": "USD", "salary_period": "hour",
                "salary_raw": f"${lo} - ${lo + 10} an hour"}
    lo = rng.choice([62_000, 75_000, 85_000, 95_000, 105_000, 120_000, 135_000, 150_000])
    hi = lo + rng.choice([10_000, 20_000, 30_000, 45_000])
    cur = "EUR" if "Germany" in location else "CAD" if "Canada" in location else "GBP" if "UK" in location else "USD"
    return {"salary_min": float(lo), "salary_max": float(hi), "salary_currency": cur, "salary_period": "year",
            "salary_is_estimate": rng.random() < 0.2, "salary_raw": f"{lo:,} - {hi:,} {cur} a year"}


def synthetic_jobs(n: int = 120, seed: int = 1, today: date | None = None) -> list[Job]:
    """Deterministic list of `n` listings (plus a handful of deliberate defects) spread over the last 45 days."""
    rng = random.Random(seed)
    today = today or date.today()
    sources = rng.choices(list(SOURCE_WEIGHTS), weights=list(SOURCE_WEIGHTS.values()), k=n)
    jobs: list[Job] = []

    def make(i: int, source: str, **over) -> Job:
        roll = rng.random()
        title = rng.choice(GOOD_TITLES) if roll < 0.78 else rng.choice(EXCLUDED_TITLES) if roll < 0.92 else rng.choice(IRRELEVANT_TITLES)
        if source == "usajobs":
            company = rng.choice(FEDERAL)
        elif rng.random() < 0.06:
            company = rng.choice(AGENCIES)
        else:
            company = rng.choice(COMPANIES)
        location = rng.choice(US_LOCATIONS) if rng.random() < 0.9 else rng.choice(FOREIGN_LOCATIONS)
        if source == "remotive":
            location = "Remote"
        posted = today - timedelta(days=min(int(rng.expovariate(1 / 9)), 45))
        desc = _description(rng) if rng.random() < 0.9 else rng.choice(["Great opportunity. Apply today.", "QA role. Details on our site."])
        job_id = f"{100000 + i * 37}"
        fields = dict(
            source=source, source_id=job_id, title=title, company=company, location=location,
            url=URL_TEMPLATES[source].format(id=job_id, slug=_slug(title), company=_slug(company)),
            posted_at=posted, posted_raw=f"{(today - posted).days} days ago", description=desc,
            employment_type=rng.choice(["Full-time", "Full-time", "Full-time", "Contract", "Part-time"]),
            seniority=rng.choice([None, "Mid-Senior level", "Entry level", "Associate"]),
            search_query=rng.choice(["QA Engineer", "Data Analyst", "Automation Engineer"]),
            **_salary(rng, location),
        )
        fields.update(over)
        return Job(**fields)

    for i, source in enumerate(sources):
        jobs.append(make(i, source))

    # Deliberate defects so every check has something to catch (appended, so the first `n` stay natural).
    base = len(jobs)
    jobs.append(make(base + 1, "greenhouse", company=None))                                        # required_fields
    jobs.append(make(base + 2, "linkedin", url="https://www.indeed.com/viewjob?jk=wronghost"))     # url_valid
    jobs.append(make(base + 3, "lever", title="Data Analyst", description=SPAM_DESCRIPTIONS[0]))    # description_quality (spam)
    jobs.append(make(base + 4, "remotive", title="QA Analyst", description=SPAM_DESCRIPTIONS[1]))
    jobs.append(make(base + 5, "adzuna", title="QA Engineer", salary_min=5_000_000.0, salary_max=6_000_000.0, salary_currency="USD", salary_period="year", salary_raw="5,000,000 - 6,000,000 USD"))  # salary_sanity
    jobs.append(make(base + 6, "indeed", title="SDET", salary_min=120_000.0, salary_max=90_000.0, salary_currency="USD", salary_period="year", salary_raw="120,000 - 90,000"))  # inverted
    jobs.append(make(base + 7, "usajobs", title="Data Analyst", posted_at=today + timedelta(days=3), posted_raw="in 3 days"))  # posting_age (future)
    jobs.append(make(base + 8, "glassdoor", title="QA Engineer", posted_at=today - timedelta(days=60), posted_raw="60 days ago"))  # posting_age (stale)
    jobs.append(make(base + 9, "lever", title="Test Automation Engineer", location="Berlin (Germany)", remote=False, description=_description(rng)))  # location_match
    dup_src = jobs[0]
    jobs.append(make(base + 10, dup_src.source, title=dup_src.title, company=dup_src.company, location=dup_src.location))  # duplicate (title/company/location)
    jobs.append(Job.from_dict(dup_src.to_dict()))                                                                          # duplicate (same id)
    return jobs


def demo_summary(run_id: str, started_at: datetime | None = None) -> RunSummary:
    """Run summary shell with two scrapers marked blocked/errored, so those UI states are visible in the demo."""
    return RunSummary(
        run_id=run_id,
        started_at=started_at or datetime.now(timezone.utc),
        blocked_sources=["glassdoor"],
        errors={"glassdoor": "bot challenge detected; this source is stopped for the run",
                "indeed": "Playwright is not installed (uv sync --extra scrapers)"},
    )


__all__ = ["synthetic_jobs", "demo_summary", "SOURCE_NAMES"]
