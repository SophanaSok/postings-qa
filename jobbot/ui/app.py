"""Streamlit entry point. Launch with `jobbot ui` (or `streamlit run jobbot/ui/app.py`)."""

from __future__ import annotations

import streamlit as st

from jobbot.ui import data
from jobbot.ui.runner import get_runner
from jobbot.ui.views import dashboard, jobs, settings

st.set_page_config(page_title="jobbot", page_icon="🧭", layout="wide", initial_sidebar_state="expanded")

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
    st.caption(
        f"**Project** `{data.project_dir()}`  \n"
        f"**Config** `{data.cfg_path().name}`  \n"
        f"**History** {data.job_count(cfg)} jobs in `{cfg.db_path}`  \n"
        + (f"**Last run** {last.started_at.astimezone():%Y-%m-%d %H:%M}" if last else "**Last run** none yet")
    )

nav.run()
