"""
Farmer app - map first, then every measured variable for the selected field.

    streamlit run farmer_app/app.py -- \
        --report farm_report.json --fields my_fields.geojson

WHY THE MAP LEADS
-----------------
A farmer knows their fields as places, not as rows in a table. The first thing
the app must answer is "which of these do I walk to", and a map answers that in
one glance where a table needs reading. Everything else hangs off selecting a
field on it.

WHY THERE IS A GREY
-------------------
A map is more persuasive than a table, and that cuts both ways: nobody reads a
colour sceptically. A field drawn confident green because nothing could be
measured on it would be a worse lie than a blank cell. So the palette has three
states, not two, and grey - not measured - is never collapsed into green or red.

Uses pydeck, which ships with Streamlit, over a Carto basemap: no map token, no
extra install.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import streamlit as st
import pydeck as pdk

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import view as D


def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--report", default="farm_report.json")
    p.add_argument("--fields", default=None)
    known, _ = p.parse_known_args()
    return known


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _centre(features):
    pts = [p for f in features for p in f["polygon"]]
    if not pts:
        return 33.0, 14.4
    return (sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))


def main():
    st.set_page_config(page_title="Farm Monitor", layout="wide")
    args = _args()

    st.title("🌾 Farm Monitor")
    st.caption("Satellite crop monitoring. Every figure states the sensor it "
               "came from and the scale it was measured at; anything that could "
               "not be measured says so rather than showing a number.")

    report_path = st.sidebar.text_input("Farm report JSON", args.report)
    fields_path = st.sidebar.text_input("Field polygons GeoJSON",
                                        args.fields or "")

    if not os.path.exists(report_path):
        st.warning(f"Report not found: {report_path}. Run the engine first:\n\n"
                   "`python src/farm_cli.py --fields my_fields.geojson "
                   "--season 2022 --out farm_report.json`")
        st.stop()

    report = _load(report_path)
    season = report.get("season", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fields", report.get("n_fields", 0))
    c2.metric("Season", f"{season.get('start','?')} → {season.get('end','?')}")
    c3.metric("Crop", report.get("crop", "—"))
    # Counted from the SAME classification the map and the list use, so the
    # header cannot disagree with the picture underneath it.
    att = D.attention_list(report)
    c4.metric("Need attention / watch",
              f"{att['n_attention']} / {att['n_watch']}")

    # ---------------------------------------------------------------- the map
    field_fc = None
    if fields_path and os.path.exists(fields_path):
        field_fc = _load(fields_path)

    if field_fc:
        feats = D.map_features(report, field_fc)
        _render_map(feats)
    else:
        st.info("Point --fields at the GeoJSON used for the run to draw the map. "
                "The measurements below do not depend on it.")

    # ------------------------------------------------------- attention order
    st.subheader("Which field first")
    if att["ranked"]:
        for r in att["ranked"]:
            with st.container():
                st.markdown(f"**{r['mark']} {r['rank']}. {r['name']}** — "
                            f"vigour {r['vigour']:.3f}")
                st.caption(r["why"])
                for d in r["drivers"]:
                    st.caption(f"• {d}")
    st.caption(att["basis"])
    if att["unmeasured"]:
        st.warning("Not measured, and therefore not ranked: "
                   + ", ".join(u["name"] for u in att["unmeasured"])
                   + ". " + att["unmeasured_note"])

    # --------------------------------------------------------- field detail
    st.subheader("Field detail")
    names = [f.get("name") for f in report.get("fields", [])]
    if not names:
        st.info("No fields in this report.")
        return
    chosen = st.selectbox("Field", names)
    rec = next(f for f in report["fields"] if f.get("name") == chosen)
    _render_field(rec)

    _render_forecast(report)

    with st.expander("What this tool does not claim"):
        for lim in report.get("limitations", []):
            st.markdown(f"- {lim}")


def _render_map(feats):
    if not feats:
        st.info("No drawable field polygons.")
        return
    lon, lat = _centre(feats)
    layer = pdk.Layer(
        "PolygonLayer", feats, get_polygon="polygon",
        get_fill_color="colour", get_line_color=[255, 255, 255, 200],
        line_width_min_pixels=1, pickable=True, auto_highlight=True)
    view = pdk.ViewState(longitude=lon, latitude=lat, zoom=11, pitch=0)
    tooltip = {"html": "<b>{name}</b><br/>status: {status}"
                       "<br/>vigour: {vigour_display}<br/>{why}"}
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view,
                             tooltip=tooltip,
                             map_style="light"))

    cols = st.columns(len(D.LEGEND))
    for col, (label, rgba, meaning) in zip(cols, D.LEGEND):
        hexcol = "#%02x%02x%02x" % tuple(rgba[:3])
        col.markdown(f"<span style='color:{hexcol};font-size:1.6em'>■</span> "
                     f"**{label}**", unsafe_allow_html=True)
        col.caption(meaning)


def _render_field(rec):
    left, right = st.columns([3, 2])

    with left:
        st.markdown("**All measured variables**")
        rows = D.variables_table(rec)
        st.dataframe(
            [{"Variable": r["variable"], "Value": r["value"],
              "Compared with": r["threshold"], "Reading": r["verdict"],
              "Sensor": r["sensor"], "Measured at": r["scale"]}
             for r in rows],
            width="stretch", hide_index=True)
        note = D.etc_method_note(rec)
        if note:
            st.caption(note)
        missing = [r for r in rows if r.get("reason")]
        for r in missing:
            st.caption(f"{r['variable']}: {r['reason']}")

        series = rec.get("series") or {}
        if series.get("status") == "OK" and series.get("dates"):
            st.markdown("**Through the season**")
            st.line_chart({"NDVI": dict(zip(series["dates"], series["ndvi"])),
                           "NDMI": dict(zip(series["dates"], series["ndmi"]))})
            st.caption(series.get("note", ""))

    with right:
        ref = rec.get("reference_provenance") or {}
        if ref.get("verdict_withheld"):
            st.warning("No threshold could be derived for this field, so the "
                       "values above are reported without a verdict. That is "
                       "not the same as a field that was checked and found "
                       "healthy.\n\n" + ref.get("reference_source", ""))

        st.markdown("**Nutrition**")
        n = D.nutrition_line(rec)
        if n["available"]:
            st.write(n["headline"])
            if n.get("next_step"):
                st.caption("To make a stronger claim: " + n["next_step"])
            st.caption(n.get("caveat", ""))
        else:
            st.write(f"Not available — {n['reason']}")

        st.markdown("**Yield**")
        st.write(D.yield_line(rec))

        st.markdown("**Advisory**")
        lang = st.radio("Advisory language", ["العربية", "English"],
                        horizontal=True, key=f"lang_{rec.get('name')}",
                        label_visibility="collapsed")
        adv = rec.get("advisory" if lang == "العربية" else "advisory_en") or {}
        for item in adv.get("items", []):
            st.markdown(f"- {item['text']}")
        if adv.get("withheld"):
            with st.expander("Not said, and why"):
                for w in adv["withheld"]:
                    st.caption(f"**{w['key']}** — {w['reason']}")
        if adv.get("rule"):
            st.caption(adv["rule"])


def _render_forecast(report):
    f = report.get("forecast") or {}
    st.subheader("7-day outlook")
    if f.get("status") != "OK":
        st.info(f"Not available — {f.get('reason', 'no forecast')}")
        return
    c1, c2 = st.columns(2)
    c1.metric("Mean temperature",
              f"{f.get('mean_temperature_c')} °C"
              if f.get("mean_temperature_c") is not None else "—")
    c2.metric("Mean rain per step",
              f"{f.get('mean_precipitation_mm_per_step')} mm"
              if f.get("mean_precipitation_mm_per_step") is not None else "—")
    st.caption((f.get("provenance", {}) or {}).get("note", "")
               + " " + f.get("caveat", ""))


if __name__ == "__main__":
    main()
