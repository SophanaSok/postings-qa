"""Jobs: filterable table over the history, with a detail panel for the selected row."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from postingsqa.sources import attributions
from postingsqa.ui import data

TABLE_COLUMNS = ["source", "title", "company", "location", "remote", "posted_at", "salary", "is_new", "first_seen", "url",
                 "qa_status", "qa_reason"]
PERIOD_LABEL = {"year": "yr", "hour": "hr", "month": "mo", "week": "wk", "day": "day"}


def _salary_text(row: pd.Series) -> str:
    """'$95,000 – $120,000 / yr', '$28 – $35 / hr', '' when unknown. Numeric columns stay in the CSV export."""
    lo, hi = row.get("salary_min"), row.get("salary_max")
    if pd.isna(lo) and pd.isna(hi):
        return ""
    cur = row.get("salary_currency") if isinstance(row.get("salary_currency"), str) else "USD"
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$"}.get(cur, f"{cur} ")
    fmt = lambda v: f"{sym}{v:,.0f}" if v >= 1000 else f"{sym}{v:,.2f}".rstrip("0").rstrip(".")
    lo_s = fmt(lo) if not pd.isna(lo) else None
    hi_s = fmt(hi) if not pd.isna(hi) else None
    amount = lo_s if hi_s is None or hi_s == lo_s else hi_s if lo_s is None else f"{lo_s} – {hi_s}"
    period = row.get("salary_period") if isinstance(row.get("salary_period"), str) else None
    est = " (est.)" if bool(row.get("salary_is_estimate")) else ""
    return f"{amount} / {PERIOD_LABEL.get(period, period)}{est}" if period else f"{amount}{est}"


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
    posted = str(row["posted_at"]) if pd.notna(row["posted_at"]) else (row["posted_raw"] if isinstance(row["posted_raw"], str) else "unknown")
    facts = [("Posted", posted), ("Salary", _salary_text(row) or "not listed"),
             ("Type", row["employment_type"] if isinstance(row["employment_type"], str) else "—"),
             ("Seniority", row["seniority"] if isinstance(row["seniority"], str) else "—")]
    # Streamlit renders `$…$` as LaTeX, so escape dollar signs in salary text.
    st.markdown(" · ".join(f"**{k}** {str(v).replace('$', chr(92) + '$')}" for k, v in facts))
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

    view = view.assign(salary=view.apply(_salary_text, axis=1))
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
            "salary": st.column_config.TextColumn("salary", width="medium"),
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
