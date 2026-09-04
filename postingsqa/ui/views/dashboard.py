"""Dashboard: KPIs from the last run, charts over the history window, run log, workbook downloads."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from postingsqa.cli import export_history
from postingsqa.sources import attributions
from postingsqa.ui import data

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _bar(df: pd.DataFrame, x: str, y: str, *, color: str | None = None, scale: dict | None = None,
         horizontal: bool = False, sort: str | list | None = None, fixed_color: str = data.PRIMARY_COLOR) -> alt.Chart:
    if color:
        enc_color = alt.Color(f"{color}:N", scale=alt.Scale(domain=list(scale), range=list(scale.values())) if scale else alt.Undefined,
                              legend=alt.Legend(title=None, orient="top"))
    else:
        enc_color = alt.value(fixed_color)
    cat = alt.X if not horizontal else alt.Y
    val = alt.Y if not horizontal else alt.X
    chart = alt.Chart(df).mark_bar(cornerRadiusEnd=2).encode(
        cat(f"{x}:N", sort=sort, title=None, axis=alt.Axis(labelAngle=0, labelLimit=180)),
        val(f"{y}:Q", title=None, axis=alt.Axis(tickMinStep=1, format="d")),
        color=enc_color,
        tooltip=[c for c in df.columns],
    )
    return chart.properties(height=260)


def _section(title: str, chart: alt.Chart | None, empty_msg: str = "No data in this window.") -> None:
    st.markdown(f"**{title}**")
    if chart is None:
        st.caption(empty_msg)
    else:
        st.altair_chart(chart, width="stretch")


def render() -> None:
    cfg = data.get_config()
    days = st.sidebar.slider("History window (days)", 7, 180, st.session_state.get("days", 30), key="days",
                             help="Jobs last seen within this many days feed the charts and the Jobs page.")

    st.title("Job Postings Dashboard")
    last = data.last_run(cfg)
    df = data.jobs_df(days, cfg)

    # -- KPIs (last run) ------------------------------------------------------------------------
    if last:
        dur = f", {(last.finished_at - last.started_at).total_seconds():.0f}s" if last.finished_at else ""
        st.caption(f"Last run `{last.run_id}` started {last.started_at.astimezone():%Y-%m-%d %H:%M}{dur}")
        k = st.columns(5)
        k[0].metric("Scraped", last.scraped)
        k[1].metric("Passed QA", last.kept)
        k[2].metric("Rejected", last.rejected)
        k[3].metric("New this run", last.new_count)
        k[4].metric("Blocked sources", len(last.blocked_sources), delta=", ".join(last.blocked_sources) or None, delta_color="inverse")
        if last.errors:
            with st.expander("Source errors from the last run"):
                for src, err in last.errors.items():
                    st.write(f"**{src}** — {err}")
    else:
        st.info("No runs recorded yet. Start one from **Settings & Run**, or run `pqa run` in a terminal.")

    if df.empty:
        st.warning(f"No jobs seen in the last {days} days.")
        return

    credits = attributions(df["source"].dropna().unique())
    st.caption(f"{len(df)} jobs seen in the last {days} days: {int((df['qa_status'] == 'kept').sum())} kept, "
               f"{int((df['qa_status'] == 'rejected').sum())} rejected, {int(df['is_new'].sum())} first seen in the latest run."
               + ("  \nListings via " + " · ".join(f"[{label}]({url})" for label, url in credits) if credits else ""))

    # -- Charts -----------------------------------------------------------------------------------
    c1, c2 = st.columns(2)
    with c1:
        _section("Jobs by source", _bar(data.by_source_status(df), "source", "jobs", color="qa_status", scale=data.STATUS_COLORS))
    with c2:
        top = data.top_companies(df)
        _section("Top companies (kept)", _bar(top, "company", "jobs", horizontal=True, sort="-x") if not top.empty else None)

    c3, c4 = st.columns(2)
    with c3:
        ppd = data.postings_per_day(df, days)
        line = None
        if not ppd.empty:
            line = alt.Chart(ppd).mark_line(point=True, color=data.PRIMARY_COLOR).encode(
                x=alt.X("day:T", title=None), y=alt.Y("jobs:Q", title=None, axis=alt.Axis(tickMinStep=1, format="d")),
                tooltip=["day:T", "jobs:Q"]).properties(height=260)
        _section("Postings per day (kept, by posted date)", line)
    with c4:
        _section("Remote vs on-site (kept)", _bar(data.remote_split(df), "kind", "jobs", fixed_color=data.CATEGORICAL_COLORS[2]))

    c5, c6 = st.columns(2)
    with c5:
        buckets = data.salary_buckets(df)
        _section("Salary distribution (kept, USD/year midpoint)",
                 _bar(buckets, "bucket", "jobs", sort=list(buckets["bucket"]), fixed_color=data.CATEGORICAL_COLORS[3]) if buckets["jobs"].sum() else None,
                 "No salary data in this window.")
    with c6:
        rej = data.rejections_df(last)
        _section("QA rejections by check (last run)", _bar(rej, "check", "jobs", horizontal=True, sort="-x", fixed_color=data.STATUS_COLORS["rejected"]) if not rej.empty else None,
                 "The last run rejected nothing.")

    # -- Runs -------------------------------------------------------------------------------------
    st.subheader("Run history")
    rdf = data.runs_df(data.runs(50, cfg))
    if rdf.empty:
        st.caption("No runs yet.")
    else:
        st.dataframe(rdf, hide_index=True, width="stretch",
                     column_config={"started": st.column_config.DatetimeColumn("started", format="YYYY-MM-DD HH:mm"),
                                    "duration_s": st.column_config.NumberColumn("duration (s)")})

    # -- Workbook ---------------------------------------------------------------------------------
    st.subheader("Excel workbook")
    w1, w2 = st.columns([1, 2])
    latest = data.latest_workbook(cfg)
    with w1:
        if latest:
            st.download_button(f"Download {latest.name}", latest.read_bytes(), file_name=latest.name, mime=XLSX_MIME, width="stretch")
            st.caption(f"Written {pd.Timestamp(latest.stat().st_mtime, unit='s').tz_localize('UTC').tz_convert(None):%Y-%m-%d %H:%M} UTC")
        else:
            st.caption(f"No workbook in `{cfg.output_dir}/` yet.")
    with w2:
        if st.button(f"Rebuild workbook from the last {days} days of history", help="Same as `pqa export --days N`: no scraping."):
            try:
                out, report, _ = export_history(cfg, days)
            except LookupError as exc:
                st.error(str(exc))
            else:
                st.success(f"Wrote `{out.relative_to(cfg.project_dir) if out.is_relative_to(cfg.project_dir) else out}`: "
                           f"{len(report.kept)} kept, {len(report.rejected)} rejected.")
                st.download_button(f"Download {out.name}", out.read_bytes(), file_name=out.name, mime=XLSX_MIME, key="dl_rebuilt")
