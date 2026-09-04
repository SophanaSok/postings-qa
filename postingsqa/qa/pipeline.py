"""Run all QA checks over a batch of jobs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from postingsqa.config import QAConfig
from postingsqa.models import Job, QAResult
from postingsqa.qa.checks import CHECKS


@dataclass
class QAReport:
    kept: list[Job] = field(default_factory=list)
    rejected: list[QAResult] = field(default_factory=list)
    results: list[QAResult] = field(default_factory=list)
    rejection_counts: Counter = field(default_factory=Counter)
    flag_counts: Counter = field(default_factory=Counter)
    per_source: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def scraped(self) -> int:
        return len(self.results)


def run_qa(jobs: list[Job], cfg: QAConfig) -> QAReport:
    report = QAReport()
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()

    for job in jobs:
        reasons: list[str] = []
        flags: list[str] = []
        failed_checks: list[str] = []

        if job.id in seen_ids:
            reasons.append("duplicate id within run")
            failed_checks.append("duplicate")
        elif job.dedupe_key in seen_keys and job.dedupe_key.strip("|"):
            reasons.append("duplicate title/company/location within run")
            failed_checks.append("duplicate")
        seen_ids.add(job.id)
        seen_keys.add(job.dedupe_key)

        for name, check in CHECKS:
            outcome = check(job, cfg)
            if outcome is None:
                continue
            if isinstance(outcome, tuple):
                flags.append(outcome[1])
                report.flag_counts[name] += 1
            else:
                reasons.append(outcome)
                failed_checks.append(name)

        result = QAResult(job=job, passed=not reasons, reasons=reasons, flags=flags)
        report.results.append(result)
        src = report.per_source.setdefault(job.source, {"scraped": 0, "kept": 0, "rejected": 0})
        src["scraped"] += 1
        if result.passed:
            report.kept.append(job)
            src["kept"] += 1
        else:
            report.rejected.append(result)
            src["rejected"] += 1
            for name in failed_checks:
                report.rejection_counts[name] += 1
    return report
