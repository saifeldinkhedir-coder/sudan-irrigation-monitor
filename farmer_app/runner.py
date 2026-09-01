"""
Running the engine from inside the app.

WHY THIS EXISTS
---------------
The largest remaining seam between this and a product was here: a farmer drew a
field on the map, saved it, and was then handed a shell command to copy into a
terminal. Every step of that is a step where a person stops.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not hide what it is running. The command is printed before it starts
and the engine's own output is streamed as it arrives - the same lines that
would appear in a terminal, including the refusals. An engine that reports
"reference too small for a threshold" for half a farm should say so where the
person who drew those boundaries can see it, not into a log nobody opens.

It also does not pretend the run is free or instant. An Earth Engine run over a
season touches thousands of scenes; the estimate is printed before the button
is pressed, and it is honest that the estimate is rough.

SUBPROCESS, NOT AN IMPORT
-------------------------
The engine is run as a child process rather than called in-process. Earth
Engine authentication, quota errors and network stalls all fail in ways that
would take the whole app down with them, and a monitoring tool that dies when a
run fails is worse than one that reports the failure. The child can also be
abandoned; an in-process call cannot.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

import streamlit as st

import ui


# Rough, and labelled rough. Measured against the demo farm: most of the time
# is per-field Earth Engine round trips, not the season length.
SECONDS_PER_FIELD = 25
SECONDS_FIXED = 40


def estimate_seconds(n_fields: int, with_series: bool = True) -> int:
    """A rough wall-clock estimate for a run.

    Deliberately an over-estimate. A progress figure that runs out before the
    work does is worse than no figure, because the reader concludes the tool
    has hung and kills it.
    """
    per = SECONDS_PER_FIELD if with_series else SECONDS_PER_FIELD * 0.6
    return int(SECONDS_FIXED + per * max(0, int(n_fields)))


def build_command(fields_path: str, season: int, crop: str, out_path: str,
                  with_series: bool = True,
                  observations: str = "observations.db") -> list:
    """The exact argument list the child process is given.

    Returned rather than executed so the app can show it, and so a test can
    assert what would run without running it.
    """
    cli = os.path.join(os.path.dirname(__file__), "..", "src", "farm_cli.py")
    cmd = [sys.executable, os.path.abspath(cli),
           "--fields", fields_path,
           "--season", str(int(season)),
           "--crop", crop,
           "--out", out_path,
           # The scouting database is passed explicitly. It is the only source
           # that can name a disease, so a run that cannot find it should say
           # which path it looked at rather than quietly producing a disease
           # layer that stops at an unnamed anomaly.
           "--observations", observations]
    if not with_series:
        cmd.append("--no-series")
    return cmd


def run(cmd: list, on_line=None, timeout: int = 3600) -> dict:
    """
    Run the engine, streaming its output.

    Returns {"returncode", "lines"}. Nothing is swallowed: the engine's refusals
    are the most useful thing it prints, and a wrapper that hides them to keep
    the screen tidy would defeat the point of the engine.
    """
    lines = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        return {"returncode": -1, "lines": [f"could not start the engine: {e}"]}

    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            if on_line:
                on_line(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        lines.append(f"the run passed {timeout} s and was stopped")
        return {"returncode": -2, "lines": lines}
    return {"returncode": proc.returncode, "lines": lines}


def panel(fields_path: Optional[str], n_fields: int, season_default: int,
          crop_default: str, ar: bool = False) -> Optional[str]:
    """
    The run control. Returns the output path when a run finishes successfully.

    Earth Engine credentials are never asked for here and never stored by this
    app. The child process uses whatever `earthengine authenticate` already put
    in the user's own home directory - which is the only place it should live.
    """
    import crops as C

    ui.section(ui.t("run_engine", ar), "", ar)
    if not fields_path or not os.path.exists(fields_path):
        ui.note(ui.t("run_needs_fields", ar), "warn", ar)
        return None

    c1, c2, c3 = st.columns([2, 2, 2])
    season = c1.number_input(ui.t("season", ar), min_value=2015,
                             max_value=2035, value=int(season_default), step=1)
    options = [k for k, _lbl in C.names(ar)]
    crop = c2.selectbox(
        ui.t("crop", ar), options,
        index=options.index(crop_default) if crop_default in options else 0,
        format_func=lambda k: C.label(k, ar),
        help=ui.t("crop_per_field_help", ar))
    out = c3.text_input(ui.t("out_file", ar), "farm_report.json")

    series = st.toggle(ui.t("with_series", ar), value=True,
                       help=ui.t("with_series_help", ar))
    cmd = build_command(fields_path, int(season), crop, out, series)
    est = estimate_seconds(n_fields, series)
    ui.note(ui.t("run_estimate", ar).format(n=n_fields, m=max(1, est // 60)),
            "", ar)
    st.code(" ".join(f'"{a}"' if " " in a else a for a in cmd), language="bash")

    if not st.button(ui.t("run_now", ar), type="primary"):
        return None

    box = st.empty()
    shown = []

    def emit(line):
        shown.append(line)
        box.code("\n".join(shown[-25:]))

    with st.spinner(ui.t("running", ar)):
        result = run(cmd, on_line=emit)

    if result["returncode"] == 0 and os.path.exists(out):
        st.success(ui.t("run_done", ar) + f" {out}")
        return out
    # The failure is shown whole. An engine that could not authenticate, or hit
    # a quota, says so in its own words, and those words are what the person
    # needs in order to fix it.
    st.error(ui.t("run_failed", ar).format(code=result["returncode"]))
    with st.expander(ui.t("run_output", ar), expanded=True):
        st.code("\n".join(result["lines"][-60:]) or "(no output)")
    return None
