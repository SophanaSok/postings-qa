"""Streamlit entry point. Launch with `pqa ui` (or `streamlit run postingsqa/ui/app.py`)."""

from __future__ import annotations

import streamlit as st

from postingsqa import __version__
from postingsqa.http import REPO_URL
from postingsqa.ui import data
from postingsqa.ui.runner import get_runner
from postingsqa.ui.views import dashboard, jobs, settings

st.set_page_config(page_title="postings-qa", page_icon="🧭", layout="wide", initial_sidebar_state="expanded")

pages = [
    st.Page(dashboard.render, title="Dashboard", icon=":material/dashboard:", default=True),
    st.Page(jobs.render, title="Jobs", icon=":material/work:", url_path="jobs"),
    st.Page(settings.render, title="Settings & Run", icon=":material/tune:", url_path="settings"),
]
nav = st.navigation(pages)

cfg = data.get_config()
runner = get_runner(str(data.project_dir()))
with st.sidebar:
    if runner.running():
        st.warning("A scrape is running", icon=":material/autorenew:")
    last = data.last_run(cfg)
    if last and last.run_id.startswith("demo-"):
        st.info("Showing **demo data** seeded by `pqa demo`. Run the bot or `pqa demo --reset` to replace it.", icon="🧪")
    st.caption(
        f"**Project** `{data.project_dir().name}`  \n"
        f"**Config** `{data.cfg_path().name}`  \n"
        f"**History** {data.job_count(cfg)} jobs in `{cfg.db_path}`  \n"
        + (f"**Last run** {last.started_at.astimezone():%Y-%m-%d %H:%M}" if last else "**Last run** none yet")
    )

    with st.expander("About postings-qa"):
        st.markdown(
            f"A job-postings pipeline that pulls listings from public APIs, runs rule-based data-QA checks with "
            f"auditable rejection reasons, keeps a deduplicated history in SQLite, and exports Excel and web dashboards.  \n"
            f"[Source on GitHub]({REPO_URL}) · v{__version__} · MIT"
        )

nav.run()
