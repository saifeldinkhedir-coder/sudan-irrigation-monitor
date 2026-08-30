"""
Farmer app - map first, then every measured variable for the selected field.

    streamlit run farmer_app/app.py -- \
        --report farm_report.json --fields my_fields.geojson

WHY THE MAP LEADS
-----------------
A farmer knows their fields as places, not as rows in a table. The first thing
the app must answer is "which of these do I walk to", and a map answers that in
one glance where a table needs reading. Everything else hangs off it.

WHY THERE IS A GREY
-------------------
A map is more persuasive than a table, and that cuts both ways: nobody reads a
colour sceptically. A field drawn confident green because nothing could be
measured on it would be a worse lie than a blank cell. So the palette has three
states, not two, and grey - not measured - is never collapsed into green or red.

Layout and copy live in ui.py, the display decisions in view.py, and this file
is the sequence of what to show. Uses pydeck, which ships with Streamlit, over a
Carto basemap: no map token, no extra install.
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
import record as R
import ui


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
    st.set_page_config(page_title="Farm Monitor", page_icon="🌾", layout="wide")
    ui.inject()
    args = _args()

    lang = st.sidebar.radio("اللغة · Language", ["العربية", "English"],
                            horizontal=True)
    ar = lang == "العربية"

    ui.hero(ar)

    report_path = st.sidebar.text_input("Farm report JSON", args.report)
    fields_path = st.sidebar.text_input("Field polygons GeoJSON",
                                        args.fields or "")

    if not os.path.exists(report_path):
        ui.note(ui.t("no_report", ar) + f" <code>{report_path}</code>",
                "stop", ar)
        st.code("python src/farm_cli.py --fields my_fields.geojson "
                "--season 2022 --out farm_report.json", language="bash")
        st.stop()

    report = _load(report_path)

    page = st.sidebar.radio(ui.t("page", ar),
                            [ui.t("page_fields", ar), ui.t("page_record", ar)])
    if page == ui.t("page_record", ar):
        R.render(report)
        return

    if report.get("note"):
        ui.note(report.get("note_ar") if ar and report.get("note_ar")
                else report["note"], "warn", ar)

    att = D.attention_list(report, ar)
    season = report.get("season", {})
    ui.stats([
        (ui.t("fields", ar), report.get("n_fields", 0), None),
        (ui.t("season", ar), str(season.get("start", "?"))[:7],
         f'→ {season.get("end", "?")}'),
        (ui.t("crop", ar), report.get("crop", "—"), None),
        (ui.t("attention_watch", ar),
         f"{att['n_attention']} / {att['n_watch']}", None),
    ])

    field_fc = (_load(fields_path)
                if fields_path and os.path.exists(fields_path) else None)
    if field_fc:
        _render_map(D.map_features(report, field_fc, ar), ar)
    else:
        ui.note(ui.t("no_map", ar), "", ar)

    # ------------------------------------------------------- attention order
    ui.section(ui.t("which_first", ar), ui.t("ranking_basis", ar), ar)
    for r in att["ranked"]:
        ui.field_card(r["rank"], r["name"],
                      r.get("status_label", r["status"]), r["status"],
                      r["vigour"], r["why"], r["drivers"], ar)
    if att["unmeasured"]:
        ui.note(ui.t("unmeasured_warn", ar)
                + ", ".join(u["name"] for u in att["unmeasured"])
                + ". " + ui.t("unmeasured_note", ar), "warn", ar)

    # --------------------------------------------------------- field detail
    ui.section(ui.t("field_detail", ar), "", ar)
    names = [f.get("name") for f in report.get("fields", [])]
    if not names:
        return
    chosen = st.selectbox(ui.t("fields", ar), names,
                          label_visibility="collapsed")
    rec = next(f for f in report["fields"] if f.get("name") == chosen)
    _render_field(rec, ar)

    _render_forecast(report, ar)

    with st.expander(ui.t("not_claimed", ar)):
        for lim in report.get("limitations", []):
            st.markdown(f"- {lim}")


def _render_map(feats, ar):
    if not feats:
        return
    lon, lat = _centre(feats)
    layer = pdk.Layer(
        "PolygonLayer", feats, get_polygon="polygon",
        get_fill_color="colour", get_line_color=[255, 255, 255, 220],
        line_width_min_pixels=1.5, pickable=True, auto_highlight=True)
    st.pydeck_chart(pdk.Deck(
        layers=[layer],
        initial_view_state=pdk.ViewState(longitude=lon, latitude=lat,
                                         zoom=11.5, pitch=0),
        tooltip={"html": "<b>{name}</b><br/>{status} · NDVI {vigour_display}"
                         "<br/>{why}"},
        map_style="light"), height=380)
    ui.legend([(key, lbl[0] if ar else lbl[1],
                meaning[0] if ar else meaning[1])
               for key, lbl, meaning in D.LEGEND_BI], ar)


def _render_field(rec, ar):
    left, right = st.columns([3, 2], gap="large")
    rows = D.variables_table(rec, ar)

    with left:
        ui.section(ui.t("all_variables", ar), "", ar)

        # The styled table by default, because it is the only one that can show
        # a below-threshold reading in red and an unavailable row in grey
        # italic. The interactive one is a click away for anyone who wants
        # sorting - st.dataframe gives that free, and it was lost when the
        # styled table replaced it.
        c1, c2 = st.columns([3, 2])
        interactive = c1.toggle(ui.t("sortable", ar), value=False,
                                key=f"tbl_{rec.get('name')}")
        c2.download_button(
            ui.t("download_csv", ar),
            data=D.rows_to_csv(rows, ar).encode("utf-8-sig"),
            file_name=f"{rec.get('name', 'field')}.csv",
            mime="text/csv", key=f"csv_{rec.get('name')}")

        if interactive:
            st.dataframe(
                [{ui.t("var", ar): r["variable"], ui.t("value", ar): r["value"],
                  ui.t("compared", ar): r["threshold"],
                  ui.t("reading", ar): r["verdict"],
                  ui.t("sensor", ar): r["sensor"],
                  ui.t("measured_at", ar): r["scale"]} for r in rows],
                width="stretch", hide_index=True)
            ui.note(ui.t("sortable_caveat", ar), "warn", ar)
        else:
            ui.variables_table(rows, ar)

        note = D.etc_method_note(rec, ar)
        if note:
            ui.note(note, "warn" if note.startswith("⚠️") else "", ar)
        for r in rows:
            if r.get("reason"):
                ui.note(f"<b>{r['variable']}</b> — {r['reason']}", "", ar)

        series = rec.get("series") or {}
        if series.get("status") == "OK" and series.get("dates"):
            ui.section(ui.t("through_season", ar), series.get("note_ar" if ar else "note", ""), ar)
            st.line_chart(
                {"NDVI": dict(zip(series["dates"], series["ndvi"])),
                 "NDMI": dict(zip(series["dates"], series["ndmi"]))},
                height=240)

    with right:
        ref = rec.get("reference_provenance") or {}
        if ref.get("verdict_withheld"):
            ui.note(ui.t("no_verdict", ar) + " "
                    + ref.get("reference_source", ""), "warn", ar)

        ui.section(ui.t("nutrition", ar), "", ar)
        n = D.nutrition_line(rec, ar)
        if n["available"]:
            st.markdown(f"**{n['headline']}**")
            if n.get("next_step"):
                ui.note(ui.t("stronger_claim", ar) + n["next_step"], "", ar)
            ui.note(n.get("caveat", ""), "", ar)
        else:
            ui.note(n["reason"], "", ar)

        ui.section(ui.t("yield_", ar), "", ar)
        ui.note(D.yield_line(rec, ar), "", ar)

        ui.section(ui.t("advisory", ar), "", ar)
        adv = rec.get("advisory" if ar else "advisory_en") or {}
        for item in adv.get("items", []):
            st.markdown(
                f'<div class="note{" rtl" if ar else ""}" '
                f'style="border-inline-start-color:{ui.STATUS_HEX["ok"]}">'
                f'{item["text"]}</div>', unsafe_allow_html=True)
        if adv.get("withheld"):
            with st.expander(ui.t("not_said", ar)):
                for w in adv["withheld"]:
                    st.caption(f"**{w['key']}** — {w['reason']}")


def _render_forecast(report, ar):
    f = report.get("forecast") or {}
    ui.section(ui.t("outlook", ar), "", ar)
    if f.get("status") != "OK":
        ui.note(f.get("reason", "—"), "", ar)
        return
    ui.stats([
        (ui.t("temperature", ar),
         f"{f.get('mean_temperature_c')} °C"
         if f.get("mean_temperature_c") is not None else "—", None),
        (ui.t("rain_step", ar),
         f"{f.get('mean_precipitation_mm_per_step')} mm"
         if f.get("mean_precipitation_mm_per_step") is not None else "—", None),
    ])
    ui.note((f.get("provenance", {}) or {}).get("note", "")
            + " " + f.get("caveat", ""), "warn", ar)


if __name__ == "__main__":
    main()
