"""
The first screen: what to do when there is nothing yet.

WHY THIS IS NOT A COSMETIC PROBLEM
----------------------------------
The app opened on a demonstration farm. That is the worst possible first
screen: it is not the reader's land, it looks like a working product, and
nothing on it tells them the way to their own fields. Somebody opening it for
the first time had two choices - believe the demo is theirs, or close the tab.

There are exactly three ways into this tool, and a first screen that names all
three is the whole feature. It is not a tour, a wizard, or a series of steps to
click through: it is one question with three answers, and every answer says
what it will cost and what it will give.

THE DEMO OPTION STAYS, AND SAYS WHAT IT IS
------------------------------------------
It is genuinely useful to see the shape of the tool before drawing anything.
But the demonstration is real satellite measurements over invented boundaries -
the most misleading combination available - so the option that opens it says so
in the same breath, not on a page the reader has to find.
"""

from __future__ import annotations

import os

import streamlit as st

import ui


DEMO_REPORT = os.path.join("docs", "farm_report_demo.json")
DEMO_FIELDS = os.path.join("docs", "gezira_fields_demo.geojson")


def needed(report_path: str, fields_path: str) -> bool:
    """Show the first screen when neither a report nor fields can be found.

    Fields alone are enough to skip it: somebody who has drawn boundaries has
    arrived, and their next step is a run, not a welcome.
    """
    return not (report_path and os.path.exists(report_path)) and \
        not (fields_path and os.path.exists(fields_path))


def render(ar: bool = False) -> dict:
    """
    One question, three answers. Returns the choice, or {} if none was made.

    {"mode": "draw"}                       - open the map with the draw tools
    {"mode": "load", ...}                  - the reader gave a path
    {"mode": "demo", "report": ..., ...}   - the demonstration data
    """
    ui.section(ui.t("welcome", ar), ui.t("welcome_sub", ar), ar)
    c1, c2, c3 = st.columns(3, gap="medium")

    with c1:
        st.markdown(f"#### {ui.t('start_draw', ar)}")
        ui.note(ui.t("start_draw_why", ar), "", ar)
        if st.button(ui.t("start_draw", ar), key="ob_draw", type="primary",
                     width="stretch"):
            return {"mode": "draw"}

    with c2:
        st.markdown(f"#### {ui.t('start_load', ar)}")
        ui.note(ui.t("start_load_why", ar), "", ar)
        path = st.text_input("GeoJSON", "", key="ob_path",
                             label_visibility="collapsed",
                             placeholder="my_fields.geojson")
        if st.button(ui.t("start_load", ar), key="ob_load", width="stretch"):
            if path and os.path.exists(path):
                return {"mode": "load", "fields": path}
            st.error(ui.t("no_report", ar) + f" {path}")

    with c3:
        st.markdown(f"#### {ui.t('start_demo', ar)}")
        # The caveat travels with the button, not on a page the reader has to
        # find. Real imagery over invented boundaries is the most misleading
        # combination available.
        ui.note(ui.t("start_demo_why", ar), "warn", ar)
        have = os.path.exists(DEMO_REPORT)
        if st.button(ui.t("start_demo", ar), key="ob_demo", disabled=not have,
                     width="stretch"):
            return {"mode": "demo", "report": DEMO_REPORT,
                    "fields": DEMO_FIELDS}
        if not have:
            st.caption(f"{DEMO_REPORT} not found")

    return {}
