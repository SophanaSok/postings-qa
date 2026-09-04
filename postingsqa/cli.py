"""Command-line interface: pqa init | run | scrape | export | stats."""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from postingsqa import __version__
from postingsqa.browser import BrowserSession
from postingsqa.config import Config, load_config, write_example
from postingsqa.export.excel import build_workbook
from postingsqa.models import Job, RunSummary
from postingsqa.qa.pipeline import QAReport, run_qa
from postingsqa.sources import get_source
from postingsqa.storage import Storage

log = logging.getLogger("postingsqa")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pqa", description="Scrape job boards, QA the listings, export an Excel dashboard.")
    p.add_argument("-c", "--config", help="path to config.yaml (default: ./config.yaml, else built-in defaults)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--version", action="version", version=f"pqa {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="write config.yaml from the bundled example").add_argument("--force", action="store_true")

    def scrape_args(sp):
        sp.add_argument("--headed", action="store_true", help="show the browser so you can clear bot challenges")
        sp.add_argument("--source", help="comma-separated subset: linkedin,indeed,glassdoor")
        sp.add_argument("--keywords", help="comma-separated search keywords (overrides config)")
        sp.add_argument("--location", help="search location (overrides config)")
        sp.add_argument("--max-pages", type=int)
        sp.add_argument("--no-details", action="store_true", help="skip detail pages (faster, no description QA)")

    run = sub.add_parser("run", help="scrape + QA + export (default workflow)")
    scrape_args(run)
    run.add_argument("--out", help="output .xlsx path")

    scrape = sub.add_parser("scrape", help="scrape + QA, store results, no Excel")
    scrape_args(scrape)

    export = sub.add_parser("export", help="rebuild the Excel workbook from stored data")
    export.add_argument("--out")
    export.add_argument("--days", type=int, default=30, help="include jobs seen within the last N days")

    sub.add_parser("stats", help="print the last run summary")

    ui = sub.add_parser("ui", help="open the web dashboard (needs `uv sync --extra ui`)")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--no-browser", action="store_true", help="don't open a browser tab automatically")
    return p


def _apply_overrides(cfg: Config, args) -> None:
    if getattr(args, "keywords", None):
        cfg.search.keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if getattr(args, "location", None):
        cfg.search.location = args.location
    if getattr(args, "max_pages", None):
        cfg.search.max_pages = args.max_pages
    if getattr(args, "no_details", False):
        cfg.search.fetch_descriptions = False
    if getattr(args, "source", None):
        wanted = {s.strip() for s in args.source.split(",")}
        cfg.sources = {name: (name in wanted) for name in cfg.sources}


def scrape_all(cfg: Config, headed: bool) -> tuple[list[Job], RunSummary]:
    summary = RunSummary(run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), started_at=datetime.now(timezone.utc))
    all_jobs: list[Job] = []
    with BrowserSession(cfg, headed=headed) as session:
        for name in cfg.enabled_sources:
            source = get_source(name)(cfg, session)
            try:
                jobs = source.run()
                if source.blocked_reason:
                    summary.blocked_sources.append(name)
                    summary.errors[name] = source.blocked_reason
            except Exception as exc:
                log.error("%s failed: %s", name, exc)
                log.debug("traceback", exc_info=True)
                summary.errors[name] = str(exc)
                jobs = []
            summary.scraped_by_source[name] = len(jobs)
            log.info("%s: %d jobs scraped", name, len(jobs))
            all_jobs.extend(jobs)
    return all_jobs, summary


def qa_and_store(cfg: Config, jobs: list[Job], summary: RunSummary) -> QAReport:
    with Storage(cfg.resolve(cfg.db_path)) as store:
        summary.new_count = store.mark_seen(jobs)
        report = run_qa(jobs, cfg.qa)
        store.record_qa(report.results)
        summary.kept_by_source = {src: stats["kept"] for src, stats in report.per_source.items()}
        summary.rejection_counts = dict(report.rejection_counts)
        summary.finished_at = datetime.now(timezone.utc)
        store.save_run(summary)
    raw_dir = cfg.resolve("data")
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"raw-{summary.run_id}.jsonl"
    with raw_path.open("w") as fh:
        for job in jobs:
            fh.write(json.dumps(job.to_dict()) + "\n")
    log.info("raw listings written to %s", raw_path)
    return report


def _out_path(cfg: Config, override: str | None) -> Path:
    if override:
        return cfg.resolve(override)
    return cfg.resolve(cfg.output_dir) / cfg.filename.format(date=datetime.now().strftime("%Y-%m-%d"), datetime=datetime.now().strftime("%Y-%m-%d_%H%M"))


def _print_summary(report: QAReport, summary: RunSummary, out: Path | None) -> None:
    print()
    print(f"Run {summary.run_id}")
    print(f"  scraped: {report.scraped}   passed QA: {len(report.kept)}   rejected: {len(report.rejected)}   new: {summary.new_count}")
    for src, n in summary.scraped_by_source.items():
        status = "BLOCKED (partial)" if src in summary.blocked_sources and n else ("BLOCKED" if src in summary.blocked_sources else ("error" if src in summary.errors else "ok"))
        kept = summary.kept_by_source.get(src, 0)
        print(f"  {src:10s} scraped {n:4d}  kept {kept:4d}  {status}")
    if summary.blocked_sources:
        print(f"  blocked: {', '.join(summary.blocked_sources)} — re-run with --headed to clear the challenge once; the browser profile keeps the cookies.")
    if report.rejection_counts:
        top = ", ".join(f"{k} {v}" for k, v in sorted(report.rejection_counts.items(), key=lambda kv: -kv[1]))
        print(f"  rejections: {top}")
    if out:
        print(f"  workbook: {out}")


def cmd_init(cfg: Config, args) -> int:
    dest = cfg.project_dir / "config.yaml"
    if write_example(dest, force=args.force):
        print(f"wrote {dest}")
        return 0
    print(f"{dest} already exists (use --force to overwrite)")
    return 1


def cmd_run(cfg: Config, args, export: bool) -> int:
    _apply_overrides(cfg, args)
    if not cfg.enabled_sources:
        log.error("no sources enabled")
        return 2
    jobs, summary = scrape_all(cfg, headed=args.headed)
    report = qa_and_store(cfg, jobs, summary)
    out = None
    if export:
        out = build_workbook(report, jobs, summary, _out_path(cfg, args.out))
    _print_summary(report, summary, out)
    return 0 if jobs else 1


def export_history(cfg: Config, days: int, out: str | Path | None = None) -> tuple[Path, QAReport, RunSummary]:
    """Rebuild the workbook from jobs seen in the last `days` days (no scraping).

    Raises LookupError when the history holds nothing in that window.
    """
    with Storage(cfg.resolve(cfg.db_path)) as store:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        jobs = store.load_jobs(since_run=since)
        summary = store.last_run()
    if not jobs:
        raise LookupError(f"no stored jobs in the last {days} days; run `pqa run` first")
    report = run_qa(jobs, cfg.qa)
    # counts describe the exported set, not the last scrape; keep the last run's blocked/error status
    export_summary = RunSummary(
        run_id=f"export-{days}d",
        started_at=datetime.now(timezone.utc),
        scraped_by_source={s: v["scraped"] for s, v in report.per_source.items()},
        kept_by_source={s: v["kept"] for s, v in report.per_source.items()},
        blocked_sources=list(summary.blocked_sources) if summary else [],
        errors=dict(summary.errors) if summary else {},
        rejection_counts=dict(report.rejection_counts),
        new_count=sum(1 for j in jobs if j.is_new),
    )
    out_path = build_workbook(report, jobs, export_summary, _out_path(cfg, str(out) if out else None))
    return out_path, report, export_summary


def cmd_export(cfg: Config, args) -> int:
    try:
        out, report, export_summary = export_history(cfg, args.days, args.out)
    except LookupError as exc:
        log.error("%s", exc)
        return 1
    _print_summary(report, export_summary, out)
    return 0


def cmd_stats(cfg: Config, args) -> int:
    with Storage(cfg.resolve(cfg.db_path)) as store:
        summary = store.last_run()
        total = store.count()
    if not summary:
        print("no runs recorded yet")
        return 1
    print(f"last run {summary.run_id} started {summary.started_at:%Y-%m-%d %H:%M} UTC")
    print(f"  scraped {summary.scraped}  kept {summary.kept}  new {summary.new_count}  blocked: {', '.join(summary.blocked_sources) or 'none'}")
    for src, n in summary.scraped_by_source.items():
        print(f"  {src:10s} scraped {n:4d}  kept {summary.kept_by_source.get(src, 0):4d}")
    print(f"  {total} jobs in history database")
    return 0


def cmd_ui(cfg: Config, args) -> int:
    if importlib.util.find_spec("streamlit") is None:
        print("the web UI needs extra packages: run `uv sync --extra ui`", file=sys.stderr)
        return 2
    app = Path(__file__).resolve().parent / "ui" / "app.py"
    env = dict(os.environ, PQA_PROJECT_DIR=str(cfg.project_dir))
    if args.config:
        env["PQA_CONFIG"] = str(Path(args.config).resolve())
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)]
    if args.no_browser:
        cmd += ["--server.headless", "true"]
    try:
        return subprocess.call(cmd, cwd=cfg.project_dir, env=env)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    if not args.verbose:
        logging.getLogger("playwright").setLevel(logging.WARNING)
    cfg = load_config(args.config)
    if args.command == "init":
        return cmd_init(cfg, args)
    if args.command == "run":
        return cmd_run(cfg, args, export=True)
    if args.command == "scrape":
        return cmd_run(cfg, args, export=False)
    if args.command == "export":
        return cmd_export(cfg, args)
    if args.command == "stats":
        return cmd_stats(cfg, args)
    if args.command == "ui":
        return cmd_ui(cfg, args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
