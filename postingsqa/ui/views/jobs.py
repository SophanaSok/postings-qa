"""Jobs: filterable table over the history, with a detail panel for the selected row."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from postingsqa.sources import attributions
from postingsqa.ui import data

TABLE_COLUMNS = ["source", "title", "company", "location", "remote", "posted_at", "salary_min", "salary_max",
                 "salary_period", "is_new", "first_seen", "url", "qa_status", "qa_reason"]


def _filters(df: pd.DataFrame) -> pd.DataFrame:
    sb = st.sidebar
    sb.markdown("**Filters**")
    status = sb.radio("QA status", ["kept", "rejected", "all"], horizontal=True, key="f_status")
    sources = sb.multiselect("Sources", sorted(df["source"].unique()), default=sorted(df["source"].unique()), key="f_sources")
    query = sb.text_input("Search title / company", key="f_query", placeholder="e.g. sdet, acme")
    remote_only = sb.toggle("Remote only", key="f_remote")
    new_only = sb.toggle("New in latest run only", key="f_new")
    with_salary = sb.toggle("Has salary", key="f_salary")

    out = df[df["source"].isin(sources)]
    if status != "all":
        out = out[out["qa_status"] == status]
    if query.strip():
        q = query.strip()
        mask = out["title"].fillna("").str.contains(q, case=False, regex=False) | out["company"].fillna("").str.contains(q, case=False, regex=False)
        out = out[mask]
    if remote_only:
        out = out[out["remote"] == True]  # noqa: E712 — column holds True/False/None
    if new_only:
        out = out[out["is_new"]]
    if with_salary:
        out = out[out["salary_min"].notna() | out["salary_max"].notna()]
    return out


def _detail(row: pd.Series) -> None:
    st.markdown(f"### {row['title'] or '(no title)'}")
    st.markdown(f"**{row['company'] or '?'}** · {row['location'] or 'location unknown'} · via {row['source']}"
                + (f" · [open posting]({row['url']})" if isinstance(row["url"], str) else ""))
    facts = st.columns(4)
    facts[0].metric("Posted", str(row["posted_at"]) if pd.notna(row["posted_at"]) else (row["posted_raw"] or "?"))
    salary = row["salary_raw"] if isinstance(row["salary_raw"], str) else "—"
    facts[1].metric("Salary", salary)
    facts[2].metric("Type", row["employment_type"] or "—")
    facts[3].metric("Seniority", row["seniority"] or "—")
    if row["qa_status"] == "rejected":
        st.error(f"Rejected: {row['qa_reason']}")
    elif row["qa_status"] == "kept":
        st.success("Passed QA")
    meta = {"id": row["id"], "search query": row["search_query"], "first seen": row["first_seen"], "last seen": row["last_seen"],
            "remote": row["remote"], "salary estimate": row["salary_is_estimate"]}
    st.caption(" · ".join(f"{k}: {v}" for k, v in meta.items() if v is not None and v != ""))
    desc = row["description"] if isinstance(row["description"], str) and row["description"].strip() else None
    with st.expander("Description", expanded=True):
        st.text(desc or "Description not fetched for this job (enable `fetch_descriptions` in Settings).")


def render() -> None:
    cfg = data.get_config()
    days = st.sidebar.slider("History window (days)", 7, 180, st.session_state.get("days", 30), key="days")
    st.title("Jobs")
    df = data.jobs_df(days, cfg)
    if df.empty:
        st.info(f"No jobs seen in the last {days} days.")
        return

    view = _filters(df)
    st.caption(f"{len(view)} of {len(df)} jobs match.")
    if view.empty:
        return

    cols = [c for c in TABLE_COLUMNS if c in view.columns]
    if st.session_state.get("f_status") == "kept":
        cols = [c for c in cols if c not in ("qa_status", "qa_reason")]
    table = view[cols].reset_index(drop=True)
    event = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        height=min(38 * (len(table) + 1), 600),
        on_select="rerun",
        selection_mode="single-row",
        key="jobs_table",
        column_config={
            "url": st.column_config.LinkColumn("url", display_text="open"),
            "salary_min": st.column_config.NumberColumn("salary min", format="dollar"),
            "salary_max": st.column_config.NumberColumn("salary max", format="dollar"),
            "posted_at": st.column_config.DateColumn("posted"),
            "first_seen": st.column_config.DatetimeColumn("first seen", format="YYYY-MM-DD"),
            "remote": st.column_config.CheckboxColumn("remote"),
            "is_new": st.column_config.CheckboxColumn("new"),
            "qa_reason": st.column_config.TextColumn("qa reason", width="large"),
        },
    )

    st.download_button("Download filtered rows as CSV", view.to_csv(index=False).encode(), file_name=f"jobs-{days}d.csv", mime="text/csv")
    credits = attributions(view["source"].dropna().unique())
    if credits:
        st.caption("Listings via " + " · ".join(f"[{label}]({url})" for label, url in credits))

    selected = event.selection.rows if event and event.selection else []
    if selected:
        _detail(view.iloc[selected[0]])
    else:
        st.caption("Select a row to see the full description and QA outcome.")
