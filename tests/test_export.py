from datetime import datetime, timezone

from openpyxl import load_workbook

from postingsqa.config import QAConfig
from postingsqa.export.excel import build_workbook
from postingsqa.models import RunSummary
from postingsqa.demo import synthetic_jobs
from postingsqa.qa.pipeline import run_qa


def test_workbook_has_sheets_and_charts(tmp_path):
    jobs = synthetic_jobs()
    report = run_qa(jobs, QAConfig())
    assert report.kept and report.rejected
    summary = RunSummary(run_id="t", started_at=datetime.now(timezone.utc), blocked_sources=["indeed"], errors={"glassdoor": "timeout"})
    out = build_workbook(report, jobs, summary, tmp_path / "out.xlsx")
    wb = load_workbook(out)
    assert wb.sheetnames == ["Dashboard", "Jobs", "Rejected", "QA Summary", "Raw", "_data"]
    assert wb["_data"].sheet_state == "hidden"
    assert len(wb["Dashboard"]._charts) >= 6
    jobs_ws = wb["Jobs"]
    assert jobs_ws.max_row == len(report.kept) + 1
    assert jobs_ws["A1"].value == "Source" and "Jobs" in jobs_ws.tables
    url_col = [c.value for c in jobs_ws[1]].index("URL") + 1
    assert jobs_ws.cell(row=2, column=url_col).hyperlink is not None
    rej = wb["Rejected"]
    assert rej.cell(row=1, column=rej.max_column).value == "Reason"
    assert rej.cell(row=2, column=rej.max_column).value
    assert wb["Raw"].max_row == len(jobs) + 1
    qa = wb["QA Summary"]
    status = {qa.cell(row=r, column=1).value: qa.cell(row=r, column=6).value for r in range(4, 4 + 12) if qa.cell(row=r, column=1).value}
    assert status["Indeed"] == "blocked" and status["Glassdoor"] == "error: timeout"
    assert status["Remotive"] == "ok"
    assert wb["Dashboard"]["A3"].value.startswith("Listings via Remotive")
