"""Settings & Run: edit config.yaml (comments preserved), preview QA changes, start/stop a scrape."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from postingsqa.config import EXAMPLE_CONFIG, SOURCE_NAMES, config_from_raw, flow_list, load_raw, save_raw
from postingsqa.qa.pipeline import QAReport
from postingsqa.ui import data
from postingsqa.ui.runner import get_runner


# -- helpers ----------------------------------------------------------------------------------

def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _text(items) -> str:
    return "\n".join(str(x) for x in (items or []))


def _section(raw, name: str):
    if raw.get(name) is None:
        raw[name] = {}
    return raw[name]


def _flatten(d, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = list(v) if isinstance(v, (list, tuple)) else v
    return out


def _save(raw, path: Path, before: dict) -> None:
    """Validate with the real loader, write, and report which keys changed."""
    try:
        config_from_raw(dict(raw), data.project_dir())
    except (TypeError, ValueError) as exc:
        st.error(f"Config rejected: {exc}")
        return
    save_raw(raw, path)
    after = _flatten(yaml.safe_load(path.read_text()) or {})
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        st.success("Saved `" + path.name + "`. Changed: " + ", ".join(f"`{k}`" for k in changed))
    else:
        st.info("Saved; nothing changed.")


def _bad_regexes(patterns: list[str]) -> list[str]:
    bad = []
    for p in patterns:
        try:
            re.compile(p, re.I)
        except re.error as exc:
            bad.append(f"`{p}` — {exc}")
    return bad


# -- config forms ------------------------------------------------------------------------------

def _search_form(raw, path: Path, before: dict) -> None:
    s = _section(raw, "search")
    with st.form("search_form", border=True):
        st.markdown("#### Search")
        c1, c2 = st.columns([2, 1])
        keywords = c1.text_area("Keywords (one per line)", _text(s.get("keywords")), height=140,
                                help="Each keyword is searched on every enabled source.")
        location = c2.text_input("Location", s.get("location", "United States"))
        max_age = c2.number_input("Max posting age (days)", 1, 365, int(s.get("max_age_days", 14)),
                                  help="Passed to the sites' own date filter.")
        c3, c4, c5 = st.columns(3)
        max_pages = c3.number_input("Result pages per keyword per source", 1, 20, int(s.get("max_pages", 3)), help="10 jobs per page. Keep small.")
        fetch = c4.toggle("Fetch descriptions (detail pages)", bool(s.get("fetch_descriptions", True)),
                          help="Slower, but enables the description-quality check.")
        max_details = c5.number_input("Max detail fetches per source", 0, 1000, int(s.get("max_details", 60)))
        if st.form_submit_button("Save search settings", type="primary"):
            kws = _lines(keywords)
            if not kws:
                st.error("At least one keyword is required.")
                return
            s["keywords"] = kws
            s["location"] = location.strip()
            s["max_age_days"] = int(max_age)
            s["max_pages"] = int(max_pages)
            s["fetch_descriptions"] = bool(fetch)
            s["max_details"] = int(max_details)
            _save(raw, path, before)


def _sources_browser_form(raw, path: Path, before: dict) -> None:
    src = _section(raw, "sources")
    b = _section(raw, "browser")
    with st.form("sources_form", border=True):
        st.markdown("#### Sources & browser")
        cols = st.columns(len(SOURCE_NAMES))
        enabled = {}
        for col, name in zip(cols, SOURCE_NAMES):
            enabled[name] = col.toggle(name, bool((src.get(name) or {}).get("enabled", True)))
        c1, c2, c3 = st.columns(3)
        headed = c1.toggle("Headed browser by default", bool(b.get("headed", False)),
                           help="Show the Chromium window. Useful to clear a Cloudflare challenge by hand once.")
        delay = c2.slider("Delay between page loads (s)", 0.0, 15.0, tuple(float(x) for x in b.get("delay_seconds", [1.5, 4.0])), step=0.5)
        timeout = c3.number_input("Page timeout (s)", 5, 300, int(b.get("timeout_seconds", 30)))
        profile = st.text_input("Browser profile directory", b.get("profile_dir", ".browser-profile"),
                                help="Persistent per-site profile; keeps challenge-clearance cookies between runs.")
        if st.form_submit_button("Save sources & browser", type="primary"):
            if not any(enabled.values()):
                st.error("Enable at least one source.")
                return
            for name, on in enabled.items():
                if src.get(name) is None:
                    src[name] = {}
                src[name]["enabled"] = bool(on)
            b["headed"] = bool(headed)
            b["delay_seconds"] = flow_list([float(delay[0]), float(delay[1])])
            b["timeout_seconds"] = int(timeout)
            b["profile_dir"] = profile.strip() or ".browser-profile"
            _save(raw, path, before)


def _qa_form(raw, path: Path, before: dict, days: int) -> None:
    q = _section(raw, "qa")
    with st.form("qa_form", border=True):
        st.markdown("#### QA filters")
        st.caption("Title must contain one *include* word and none of the *exclude* words (whole word, case-insensitive). "
                   "Patterns are regular expressions.")
        c1, c2, c3 = st.columns(3)
        include = c1.text_area("Include keywords", _text(q.get("include_keywords")), height=170)
        exclude = c2.text_area("Exclude keywords", _text(q.get("exclude_keywords")), height=170)
        locations = c3.text_area("Accepted location substrings", _text(q.get("locations")), height=170,
                                 help="Blank list accepts every location. `, ` matches 'City, ST'.")
        c4, c5, c6, c7 = st.columns(4)
        remote_ok = c4.toggle("Remote jobs pass location check", bool(q.get("remote_ok", True)))
        max_age = c5.number_input("Reject older than (days)", 1, 365, int(q.get("max_age_days", 30)))
        min_desc = c6.number_input("Min description length (chars)", 0, 5000, int(q.get("min_description_chars", 200)))
        bounds = q.get("salary_bounds_usd_year", [20000, 500000])
        lo = c7.number_input("Salary floor (USD/yr)", 0, 10_000_000, int(bounds[0]), step=5000)
        hi = c7.number_input("Salary ceiling (USD/yr)", 0, 10_000_000, int(bounds[1]), step=5000)
        c8, c9, c10 = st.columns(3)
        blocked = c8.text_area("Blocked companies", _text(q.get("blocked_companies")), height=130)
        agency = c9.text_area("Staffing-agency patterns (regex on company)", _text(q.get("agency_patterns")), height=130)
        spam = c10.text_area("Spam patterns (regex on description)", _text(q.get("spam_patterns")), height=130)

        b1, b2 = st.columns([1, 1])
        preview = b1.form_submit_button(f"Preview against the last {days} days", help="Re-runs the QA checks on stored jobs with these values. Nothing is saved or scraped.")
        save = b2.form_submit_button("Save QA settings", type="primary")

    if not (preview or save):
        return
    candidate = {
        "include_keywords": _lines(include),
        "exclude_keywords": _lines(exclude),
        "remote_ok": bool(remote_ok),
        "locations": _lines(locations),
        "max_age_days": int(max_age),
        "min_description_chars": int(min_desc),
        "salary_bounds_usd_year": [int(lo), int(hi)],
        "blocked_companies": _lines(blocked),
        "agency_patterns": _lines(agency),
        "spam_patterns": _lines(spam),
    }
    problems = _bad_regexes(candidate["agency_patterns"] + candidate["spam_patterns"])
    if not candidate["include_keywords"]:
        problems.append("include keywords cannot be empty (every title would be rejected)")
    if lo >= hi:
        problems.append("salary floor must be below the ceiling")
    if problems:
        st.error("Fix before saving:\n\n" + "\n".join(f"- {p}" for p in problems))
        return
    if preview:
        _qa_preview(candidate, days)
    if save:
        for k, v in candidate.items():
            q[k] = flow_list(v) if k == "salary_bounds_usd_year" else v
        _save(raw, path, before)


def _qa_preview(candidate: dict, days: int) -> None:
    current_raw = load_raw(data.cfg_path(), data.project_dir()).get("qa") or {}
    now: QAReport | None = data.preview_qa(dict(current_raw), days)
    new: QAReport | None = data.preview_qa(candidate, days)
    if not new or not new.results:
        st.info("No stored jobs to preview against.")
        return
    st.markdown("**QA preview** (stored jobs, current settings → proposed)")
    m = st.columns(3)
    m[0].metric("Jobs checked", new.scraped)
    m[1].metric("Kept", len(new.kept), delta=len(new.kept) - (len(now.kept) if now else 0))
    m[2].metric("Rejected", len(new.rejected), delta=len(new.rejected) - (len(now.rejected) if now else 0), delta_color="inverse")
    checks = sorted(set(new.rejection_counts) | set(now.rejection_counts if now else {}))
    if checks:
        table = pd.DataFrame({"check": checks,
                              "current": [now.rejection_counts.get(c, 0) if now else 0 for c in checks],
                              "proposed": [new.rejection_counts.get(c, 0) for c in checks]})
        table["change"] = table["proposed"] - table["current"]
        st.dataframe(table, hide_index=True, width="content")
    now_ids = {j.id for j in now.kept} if now else set()
    gained = [r.job for r in new.results if r.passed and r.job.id not in now_ids]
    lost = [r for r in new.results if not r.passed and r.job.id in now_ids]
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"Would newly keep {len(gained)}")
        for j in gained[:15]:
            st.write(f"- {j.title} — {j.company}")
    with c2:
        st.caption(f"Would newly reject {len(lost)}")
        for r in lost[:15]:
            st.write(f"- {r.job.title} — {r.job.company}: *{r.reason}*")


def _paths_form(raw, path: Path, before: dict) -> None:
    storage = _section(raw, "storage")
    export = _section(raw, "export")
    with st.form("paths_form", border=True):
        st.markdown("#### Storage & export")
        c1, c2, c3 = st.columns(3)
        db = c1.text_input("SQLite history", storage.get("db_path", "data/jobs.db"))
        out_dir = c2.text_input("Workbook directory", export.get("output_dir", "output"))
        fname = c3.text_input("Workbook filename", export.get("filename", "jobs-{date}.xlsx"), help="`{date}` → YYYY-MM-DD, `{datetime}` → YYYY-MM-DD_HHMM")
        if st.form_submit_button("Save paths", type="primary"):
            storage["db_path"] = db.strip() or "data/jobs.db"
            export["output_dir"] = out_dir.strip() or "output"
            export["filename"] = fname.strip() or "jobs-{date}.xlsx"
            _save(raw, path, before)


def _raw_editor(path: Path, before: dict) -> None:
    with st.expander("Raw YAML"):
        current = path.read_text() if path.exists() else EXAMPLE_CONFIG.read_text()
        text = st.text_area("config.yaml", current, height=420, key="raw_yaml", label_visibility="collapsed")
        c1, c2 = st.columns([1, 3])
        if c1.button("Save raw YAML"):
            try:
                parsed = yaml.safe_load(text) or {}
                if not isinstance(parsed, dict):
                    raise ValueError("top level must be a mapping")
                config_from_raw(parsed, data.project_dir())
            except (yaml.YAMLError, TypeError, ValueError) as exc:
                st.error(f"Not saved: {exc}")
            else:
                path.write_text(text if text.endswith("\n") else text + "\n")
                after = _flatten(parsed)
                changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
                st.success("Saved. Changed: " + (", ".join(f"`{k}`" for k in changed) or "nothing"))
        with c2.popover("Reset to example config"):
            st.write("Overwrite `config.yaml` with `config.example.yaml`? Your edits are lost.")
            if st.button("Yes, reset", type="primary"):
                path.write_text(EXAMPLE_CONFIG.read_text())
                st.session_state.pop("raw_yaml", None)
                st.rerun()


# -- run panel ---------------------------------------------------------------------------------

def _run_panel(days: int) -> None:
    cfg = data.get_config()
    runner = get_runner(str(data.project_dir()))
    running = runner.running()
    st.subheader("Run the bot")

    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 1, 2])
        mode = c1.radio("Mode", ["run", "scrape"], captions=["scrape → QA → Excel", "scrape → QA, no Excel"], disabled=running, key="run_mode")
        headed = c2.toggle("Headed browser", value=cfg.browser.headed, disabled=running, key="run_headed",
                           help="Opens a visible Chromium on this desktop so you can clear a bot challenge by hand.")
        sources = c3.multiselect("Sources for this run", list(SOURCE_NAMES), default=cfg.enabled_sources, disabled=running, key="run_sources")
        with st.expander("One-off overrides (config.yaml is not changed)"):
            o1, o2, o3 = st.columns([2, 1, 1])
            kw = o1.text_input("Keywords (comma-separated)", disabled=running, key="run_keywords", placeholder=", ".join(cfg.search.keywords))
            loc = o2.text_input("Location", disabled=running, key="run_location", placeholder=cfg.search.location)
            pages = o3.number_input("Max pages (0 = config)", 0, 20, 0, disabled=running, key="run_pages")
            no_details = st.toggle("Skip detail pages (--no-details)", disabled=running, key="run_nodetails")

        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("Start", type="primary", disabled=running or not sources, width="stretch"):
            args = [mode]
            if headed:
                args.append("--headed")
            if set(sources) != set(cfg.enabled_sources):
                args += ["--source", ",".join(sources)]
            if kw.strip():
                args += ["--keywords", kw.strip()]
            if loc.strip():
                args += ["--location", loc.strip()]
            if pages:
                args += ["--max-pages", str(int(pages))]
            if no_details:
                args.append("--no-details")
            cfg_arg = data.cfg_path() if data.cfg_path().exists() else None
            runner.start(args, cfg_arg)
            st.rerun()
        if b2.button("Stop", disabled=not running, width="stretch"):
            runner.stop()
            st.rerun()

    _log_panel(runner, running)


def _log_panel(runner, running: bool) -> None:
    if runner.log_path is None:
        logs = runner.past_logs()
        if logs:
            pick = st.selectbox("Earlier run logs", logs, format_func=lambda p: p.stem)
            st.code(runner.tail(200, pick) or "(empty)", language="log", height=300)
        return

    @st.fragment(run_every=2 if running else None)
    def live() -> None:
        rc = runner.returncode
        if rc is None:
            elapsed = time.time() - runner.started_at.timestamp()
            st.info(f"Running for {elapsed:,.0f}s: `{' '.join(runner.command[3:])}`", icon=":material/autorenew:")
        elif rc == 0:
            st.success(f"Finished OK: `{' '.join(runner.command[3:])}`")
        else:
            st.error(f"Exited with code {rc}: `{' '.join(runner.command[3:])}` (1 = no jobs scraped, see log)")
        st.code(runner.tail(200) or "(waiting for output…)", language="log", height=360)
        if rc is not None and running:
            st.rerun(scope="app")  # process just ended: redraw with the fragment timer off and fresh KPIs

    live()

    rc = runner.returncode
    if rc is not None:
        cfg = data.get_config()
        last = data.last_run(cfg)
        if last and runner.started_at and last.started_at >= runner.started_at.replace(microsecond=0):
            k = st.columns(4)
            k[0].metric("Scraped", last.scraped)
            k[1].metric("Passed QA", last.kept)
            k[2].metric("New", last.new_count)
            k[3].metric("Blocked", ", ".join(last.blocked_sources) or "none")
        wb = data.latest_workbook(cfg)
        if wb and wb.stat().st_mtime >= runner.started_at.timestamp():
            st.download_button(f"Download {wb.name}", wb.read_bytes(), file_name=wb.name,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# -- page --------------------------------------------------------------------------------------

def render() -> None:
    days = st.sidebar.slider("History window (days)", 7, 180, st.session_state.get("days", 30), key="days",
                             help="Used by the QA preview.")
    st.title("Settings & Run")
    path = data.cfg_path()
    if not path.exists():
        st.warning(f"`{path.name}` does not exist yet; showing the bundled example. Saving any section creates it.")

    tab_run, tab_cfg = st.tabs(["Run", "Configuration"])
    with tab_run:
        _run_panel(days)
    with tab_cfg:
        raw = load_raw(path, data.project_dir())
        before = _flatten(yaml.safe_load(path.read_text()) if path.exists() else yaml.safe_load(EXAMPLE_CONFIG.read_text()))
        _search_form(raw, path, before)
        _sources_browser_form(raw, path, before)
        _qa_form(raw, path, before, days)
        _paths_form(raw, path, before)
        _raw_editor(path, before)
