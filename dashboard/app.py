"""
Scheme-manager dashboard (Stage 2).

    streamlit run dashboard/app.py -- --results docs/sample_results.json

Reads the engine's results JSON and presents the manager phrasing of every
number: a canal table ordered by the question a manager actually asks - which
canals to look at first - with each canal expanding to its head-to-tail reach
profile, its nutrition at the honest claim level, and the full provenance behind
every figure.

Design commitments that mirror the engine's integrity rules:
  - A NOT AVAILABLE indicator is shown as "not available", never as 0 or blank.
  - A flagged canal shows its CONFIDENCE INTERVAL, not just a point gap.
  - Every equity panel carries the attribution caveat: a gap is a measured
    difference, attributed to no one.
  - A weak (near-unimodal) irrigated-extent figure is marked unreliable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
import data as D


def _load_results(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--results", default="docs/sample_results.json")
    # Streamlit passes its own args; parse only ours.
    known, _ = p.parse_known_args()
    return known


def main():
    st.set_page_config(page_title="Sudan Irrigation Monitor — Manager",
                       layout="wide")
    args = _args()

    st.title("Sudan Irrigation & Agriculture Monitor")
    st.caption("Scheme-manager view — network layer first. "
               "Every figure describes measured condition and attributes nothing "
               "to any office, operator or decision.")

    results_path = st.sidebar.text_input("Results JSON", args.results)
    if not os.path.exists(results_path):
        st.warning(f"Results file not found: {results_path}. Run the engine first "
                   "(see README), or point to docs/sample_results.json.")
        st.stop()

    results = _load_results(results_path)
    if results.get("note"):
        st.info(results["note"])

    rows = D.canal_rows(results)
    season = results.get("season", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Canals", len(rows))
    col2.metric("Flagged for review", D.flagged_count(rows))
    col3.metric("Season", f"{season.get('start','?')} → {season.get('end','?')}")
    col4.metric("Crop", results.get("crop", "—"))

    sort_by = st.sidebar.selectbox("Sort canals by",
                                   ["gap", "water", "name"], index=0)
    rows = D.sort_canals(rows, sort_by)

    st.subheader("Canals")
    st.caption("Flagged first, then by head-to-tail gap. A flag means the 95% "
               "lower bound of the gap exceeds the review threshold — a gradient "
               "we are confident is real, not a noisy point estimate.")

    # summary table
    st.dataframe(
        [{"": "🚩" if r["flagged"] else "",
          "Canal": r["name"],
          "Head–tail gap": r["gap_display"],
          "Gap 95% CI": r["gap_ci_display"],
          "Equity": r["equity_status"],
          "Canal water": r["water_display"],
          "Irrigated extent": r["extent_display"],
          "Extent reliable": ("—" if r["extent_reliable"] is None
                              else ("yes" if r["extent_reliable"] else "WEAK")),
          "Command area": r["command_source"]}
         for r in rows],
        use_container_width=True, hide_index=True)

    st.subheader("Canal detail")
    by_name = {c.get("name"): c for c in results.get("canals", [])}
    for r in rows:
        canal = by_name.get(r["name"], {})
        flag = "🚩 " if r["flagged"] else ""
        with st.expander(f"{flag}{r['name']}  ·  gap {r['gap_display']} "
                         f"(CI {r['gap_ci_display']})"):
            _render_canal(canal)

    _render_fields(results)
    _render_rangeland(results)
    _render_forecast(results)


def _render_fields(results: dict):
    """The field layer. Absent unless field polygons were supplied - there is no
    honest way to invent a field boundary, so the section says so and stops."""
    st.subheader("Fields")
    if not results.get("field_geometry_supplied"):
        st.info("No field polygons were supplied, so the field layer did not "
                "run. Pass --fields to the engine to enable it.")
        return

    frows = D.field_rows(results)
    no_verdict = D.fields_without_verdict(frows)
    c1, c2 = st.columns(2)
    c1.metric("Fields", len(frows))
    c2.metric("Measured, no verdict", no_verdict)
    if no_verdict:
        st.warning(
            f"{no_verdict} of {len(frows)} fields have values but no stress "
            "verdict: no surrounding area was wide enough to derive a threshold "
            "from. These are measurements without a judgement — not fields that "
            "were checked and found healthy. Supplying real command-area "
            "polygons is what fixes this, not more imagery.")

    by_field = {f.get("name"): f for f in results.get("fields", [])}
    st.dataframe(
        [{"Field": r["name"],
          "Vigour (NDVI)": r["vigour_display"],
          "Canopy moisture": r["moisture_display"],
          "Thermal": r["thermal_display"],
          "Rain 14d (mm)": "—" if r["rainfall_14d"] is None else r["rainfall_14d"],
          "Water needed": (D.water_requirement_summary(
              by_field.get(r["name"], {})).get("headline", "not available")
              if D.water_requirement_summary(by_field.get(r["name"], {}))["available"]
              else "not available"),
          "Verdict": r["verdict"],
          "Reference": r["reference_source"],
          "Nutrition": (r["nutrition"]["headline"] if r["nutrition"]["available"]
                        else "not available")}
         for r in frows],
        use_container_width=True, hide_index=True)
    st.caption("‘Water needed’ is crop water REQUIREMENT calculated from "
               "weather and canopy. Nothing here measures what any field "
               "actually received.")


def _render_rangeland(results: dict):
    rows = D.rangeland_rows(results)
    if not rows:
        return
    st.subheader("Rangeland")
    st.warning(
        "These figures describe vegetation and surface water only. They say "
        "nothing about who may use this land, who has used it, or who should. "
        "They are neutral information for every party and are not evidence of "
        "any claim.")
    refused = [r for r in rows if r["status"] == "REFUSED"]
    if refused:
        for r in refused:
            st.error(f"{r['name']}: {r['reason']}")
    st.dataframe(
        [{"Area": r["name"],
          "Status": r["status"],
          "Seasonal greenness": r["productivity"],
          "vs this site's history": r["verdict"],
          "Green-up day": r["greenup_day"],
          "Surface water": r["water"]}
         for r in rows],
        use_container_width=True, hide_index=True)


def _render_forecast(results: dict):
    f = D.forecast_summary(results)
    st.subheader("7-day outlook")
    if not f["available"]:
        st.info(f"Not available: {f['reason']}")
        return
    c1, c2 = st.columns(2)
    c1.metric("Mean temperature", f"{f['temperature_c']} °C"
              if f["temperature_c"] is not None else "—")
    c2.metric("Horizon", f"{f['horizon_days']} days")
    st.caption(f["caveat"] + " A forecast is not a measurement, and no alert "
                             "in this platform is raised from it.")


def _render_canal(canal: dict):
    left, right = st.columns([3, 2])

    with left:
        st.markdown("**Head-to-tail reach profile**")
        rs = D.reach_series(canal)
        if not rs["available"]:
            st.write(f"Not available — {rs['reason']}")
        else:
            st.line_chart(
                {"NDVI along canal (0 = head, 1 = tail)":
                 dict(zip([f"{p:.2f}" for p in rs["positions"]], rs["ndvi"]))})
            if rs["gap"] is not None:
                ci = rs["ci"]
                ci_txt = (f" (95% CI {100*ci[0]:.0f}%…{100*ci[1]:.0f}%)"
                          if ci and ci[0] is not None else "")
                st.write(f"Gap head→tail: **{100*rs['gap']:.0f}%**{ci_txt}, "
                         f"fit R² {rs['r2']}.")
            st.caption(rs["caveat"])

    with right:
        st.markdown("**Canal water (Sentinel-1)**")
        cw = canal.get("canal_water", {})
        if cw.get("status") == "OK":
            st.write(f"Water fraction: {cw['value']}")
        else:
            st.write(f"Not available — {cw.get('reason','')}")
        for line in D.provenance_lines(cw):
            st.caption(line)

        st.markdown("**Nutrition**")
        n = D.nutrition_summary(canal)
        if n["available"]:
            st.write(n["headline"])
            st.caption(n["caveat"])
        else:
            st.write(f"Not available — {n['reason']}")

        st.markdown("**Climate**")
        clim = canal.get("climate", {})
        if clim and clim.get("season_vs_history"):
            svh = clim["season_vs_history"]
            st.write(f"Rainfall: {svh.get('verdict','—')} "
                     f"({svh.get('this_season_mm','—')} mm, "
                     f"{svh.get('percentile','—')}th percentile)")
        ds = (clim or {}).get("dry_spells")
        if ds:
            st.write(f"Longest dry spell: {ds.get('longest_dry_spell_days','—')} days")

    st.markdown("**Irrigated extent**")
    ext = canal.get("irrigated_extent", {})
    if ext.get("status") == "OK":
        st.write(f"Cropped fraction: {ext['value']}")
        for line in D.provenance_lines(ext):
            st.caption(line)
    else:
        st.write(f"Not available — {ext.get('reason','')}")


if __name__ == "__main__":
    main()
