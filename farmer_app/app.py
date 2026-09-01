"""
Farmer app - find the field, see its state, read its numbers.

    streamlit run farmer_app/app.py -- \
        --report farm_report.json --fields my_fields.geojson

THE SHAPE OF THE SCREEN
-----------------------
Search bar, then map beside list, then the detail of one field. That order is
not a style choice. A farmer knows their land as places, and a scheme officer
knows it as a list of tenancies; the two enter from opposite ends and must meet
on the same object. So the map and the list are side by side, they are driven
by the same filter, and clicking either selects the same field.

WHERE THE METHOD WENT
---------------------
Off this screen and onto the "About the data" page, whole. The rule applied to
every paragraph:

    Would knowing this change what the reader DOES today?

"Not measured is not healthy" survives that test - you go and look. "Green-up
is the first crossing of half the seasonal amplitude" does not. The first kind
stayed inline; the second kind moved. Nothing was deleted: a tool whose case
rests on auditable numbers cannot bury the basis for them.

WHY THERE IS A GREY
-------------------
A map is more persuasive than a table, and that cuts both ways: nobody reads a
colour sceptically. A field drawn confident green because nothing could be
measured on it would be a worse lie than a blank cell. So the palette has three
states, not two, and grey - not measured - is never collapsed into green or red.

Layout and copy live in ui.py, display decisions in view.py, search in
search.py, the map in fieldmap.py, and this file is the sequence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import view as D
import record as R
import about as A
import search as S
import ui
import fieldmap as FM


def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--report", default="farm_report.json")
    p.add_argument("--fields", default=None)
    known, _ = p.parse_known_args()
    return known


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    st.set_page_config(page_title="Farm Monitor", page_icon="🌾",
                       layout="wide")
    ui.inject()
    args = _args()

    lang = st.sidebar.radio("اللغة · Language", ["العربية", "English"],
                            horizontal=True)
    ar = lang == "العربية"

    report_path = st.sidebar.text_input("Farm report JSON", args.report)
    fields_path = st.sidebar.text_input("Field polygons GeoJSON",
                                        args.fields or "")

    if not os.path.exists(report_path):
        ui.topbar(ar)
        ui.note(ui.t("no_report", ar) + f" <code>{report_path}</code>",
                "stop", ar)
        st.code("python src/farm_cli.py --fields my_fields.geojson "
                "--season 2022 --out farm_report.json", language="bash")
        st.stop()

    report = _load(report_path)
    season = report.get("season", {})

    # The demo label is a chip, not a paragraph. Engine output over invented
    # boundaries has to say what it is - real imagery with made-up field edges
    # is the most misleading combination available - but four words on the
    # header do that, and the full statement is on the About page.
    ui.topbar(ar, tags=[
        f'{ui.t("season", ar)} {str(season.get("start", "?"))[:7]}'
        f' → {str(season.get("end", "?"))[:7]}',
        f'{ui.t("crop", ar)} · {report.get("crop", "—")}',
        f'{report.get("n_fields", 0)} {ui.t("fields", ar)}',
    ], demo=bool(report.get("note")))

    page = st.sidebar.radio(ui.t("page", ar),
                            [ui.t("page_fields", ar), ui.t("page_record", ar),
                             ui.t("page_about", ar)])
    if page == ui.t("page_record", ar):
        R.render(report)
        return
    if page == ui.t("page_about", ar):
        A.render(report, ar)
        return

    field_fc = (_load(fields_path)
                if fields_path and os.path.exists(fields_path) else None)
    index = S.field_index(report, field_fc, ar=ar)

    counts = S.status_counts(index)
    ui.stats([
        (ui.t("fields", ar), len(index), None),
        (D.label(D.STATUS_LABEL, "attention", ar), counts["attention"], None),
        (D.label(D.STATUS_LABEL, "watch", ar), counts["watch"], None),
        (D.label(D.STATUS_LABEL, "unmeasured", ar), counts["unmeasured"], None),
    ])

    criteria = _toolbar(index, ar)
    use_area = criteria.pop("area")
    criteria["polygon"] = st.session_state.get("drawn_poly") if use_area else None
    result = S.filter_fields(index, **criteria)
    matched = {r["name"] for r in result["matched"]}

    # ------------------------------------------------ map beside the list
    # Streamlit lays columns out left-to-right whatever the page direction, so
    # in Arabic the map is put in the SECOND column to land on the right, where
    # the eye starts. A right-to-left page whose main object sits on the left
    # reads as an English layout with Arabic text poured into it.
    cols = st.columns([2, 3] if ar else [3, 2], gap="large")
    col_map, col_list = (cols[1], cols[0]) if ar else (cols[0], cols[1])

    with col_map:
        feats = D.map_features(report, field_fc, ar) if field_fc else []
        state = FM.render(feats, _map_centre(feats), key="main_map",
                          drawing=True, highlight=matched if feats else None)
        if feats:
            ui.legend([(key, lbl[0] if ar else lbl[1],
                        meaning[0] if ar else meaning[1])
                       for key, lbl, meaning in D.LEGEND_BI], ar)
        else:
            ui.note(ui.t("no_map", ar), "warn", ar)

    st.session_state["drawn_poly"] = FM.last_drawn_polygon(state)

    # A click on a field selects it. Resolved by point-in-polygon, so a click
    # on bare ground selects nothing rather than the nearest field.
    click = (state or {}).get("last_object_clicked") or {}
    hit = S.field_at_point(index, click.get("lat"), click.get("lng"))
    if hit:
        st.session_state["sel"] = hit

    with col_list:
        chosen = _field_list(result, ar)

    _handle_drawings(state, ar)

    # ----------------------------------------------------------- field detail
    if not chosen:
        return
    rec = next((f for f in report.get("fields", [])
                if f.get("name") == chosen), None)
    if rec is None:
        return
    ui.section(f'{ui.t("field_detail", ar)} — {chosen}', "", ar)
    _render_field(rec, ar)
    _render_forecast(report, ar)


# ==============================================================================
# SEARCH AND LIST
# ==============================================================================

def _toolbar(index, ar) -> dict:
    """The search bar. Returns the criteria as filter_fields keyword arguments.

    Kept on the working screen rather than behind a menu: a scheme has tens of
    thousands of tenancies, and a search you have to find first is a search
    nobody uses.
    """
    c1, c2, c3, c4 = st.columns([3, 2, 2, 1])

    # Called before the inputs are instantiated, though it is drawn beside
    # them: a widget's session-state entry cannot be cleared once the widget
    # for it has been created in the same run. Columns place their contents by
    # position, not by call order, so the reset can be first in the code and
    # last on the screen.
    c4.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
    if c4.button(ui.t("clear_filters", ar), width="stretch"):
        for k in ("q", "crops", "sts", "dbasis", "dfrom", "dto", "harv",
                  "usearea"):
            st.session_state.pop(k, None)

    q = c1.text_input(ui.t("search", ar), key="q",
                      placeholder=ui.t("search_ph", ar))
    crops = c2.multiselect(ui.t("crop_filter", ar), S.crops_in(index),
                           key="crops")
    status_opts = ["attention", "watch", "ok", "unmeasured"]
    sts = c3.multiselect(
        ui.t("status_filter", ar), status_opts, key="sts",
        format_func=lambda k: D.label(D.STATUS_LABEL, k, ar))

    d1, d2, d3, d4, d5 = st.columns([2, 2, 2, 2, 2])
    basis_opts = ["greenup_date", "harvest_date", "last_seen", "sown_date"]
    basis_lbl = {"greenup_date": "d_greenup", "harvest_date": "d_harvest",
                 "last_seen": "d_last_seen", "sown_date": "d_sown"}
    dbasis = d1.selectbox(ui.t("date_basis", ar), basis_opts, key="dbasis",
                          format_func=lambda k: ui.t(basis_lbl[k], ar))
    dfrom = d2.date_input(ui.t("date_from", ar), value=None, key="dfrom",
                          format="YYYY-MM-DD")
    dto = d3.date_input(ui.t("date_to", ar), value=None, key="dto",
                        format="YYYY-MM-DD")
    hmap = {ui.t("h_any", ar): None, ui.t("h_done", ar): "harvested",
            ui.t("h_none", ar): "not_reported"}
    harv = hmap[d4.selectbox(ui.t("harvest_filter", ar), list(hmap), key="harv")]
    use_area = d5.toggle(ui.t("area_filter", ar), key="usearea",
                         help=ui.t("area_hint", ar))

    return {"text": q or "", "crops": crops, "statuses": sts,
            "date_field": dbasis, "date_from": dfrom, "date_to": dto,
            "harvest": harv, "area": bool(use_area)}


# Worst first. This is the ordering the old "which field first" section carried
# in prose; it is now the order of the list itself, which is where an ordering
# belongs. It is an ordering, not a score - no calibrated health scale exists
# and one is not invented here - and unmeasured sorts last rather than best,
# because a field nobody could see is not a field that passed.
STATUS_RANK = {"attention": 0, "watch": 1, "ok": 2, "unmeasured": 3}


def _field_list(result, ar) -> str:
    """The filtered fields, worst first, and the selector for the detail."""
    ui.result_count(result["n_matched"], result["n_total"], ar)

    rows = sorted(result["matched"],
                  key=lambda r: (STATUS_RANK.get(r["status"], 9),
                                 r["vigour"] if r["vigour"] is not None else 9))
    names = [r["name"] for r in rows]
    if not names:
        ui.note(ui.t("no_match", ar), "warn", ar)
        chosen = ""
    else:
        # No `key=` on the selectbox. A keyed widget reads its value from
        # session_state, and session_state can hold a field the current filter
        # has removed from the options - which raises. Selection is held by
        # hand so a map click, a filter change and the menu can all set it.
        sel = st.session_state.get("sel")
        if sel not in names:
            sel = names[0]
        chosen = st.selectbox(ui.t("selected", ar), names,
                              index=names.index(sel),
                              help=ui.t("click_map", ar))
        st.session_state["sel"] = chosen

        st.markdown('<div class="panel">', unsafe_allow_html=True)
        for r in rows:
            tags = [r["crop"]] if r.get("crop") else []
            if r.get("area_ha"):
                tags.append(f'{r["area_ha"]} {ui.t("ha", ar)}')
            if r.get("harvest_date"):
                mark = (ui.t("reported", ar)
                        if r["harvest_source"] == "REPORTED"
                        else ui.t("est", ar))
                tags.append(f'{ui.t("d_harvest", ar)} {r["harvest_date"]}'
                            f' · {mark}')
            ui.field_row(
                r["name"], r["status"],
                D.label(D.STATUS_LABEL, r["status"], ar), tags,
                right=("NDVI %.3f" % r["vigour"]) if r["vigour"] is not None
                else "—",
                sub=r["why"], selected=r["name"] == chosen, ar=ar)
        st.markdown("</div>", unsafe_allow_html=True)

        # Stays on the working screen: it changes what the reader does with a
        # grey row - go and look - rather than explaining how a number was got.
        if any(r["status"] == "unmeasured" for r in rows):
            ui.note(ui.t("unmeasured_note", ar), "warn", ar)

    # Fields set aside because the value being filtered on was never recorded.
    # Shown, not dropped: "no crop recorded" and "not this crop" are different
    # facts, and a filter that merges them lies quietly.
    if result["unknown"]:
        ui.note(ui.t("unknown_bucket", ar), "warn", ar)
        for u in result["unknown"]:
            st.caption(f'{u["name"]} — {ui.t("u_" + u["unknown_because"], ar)}')

    return chosen


# ==============================================================================
# MAP
# ==============================================================================

def _map_centre(feats):
    """Centre on the drawn fields if there are any, else on Gezira - a sensible
    place for this tool to open rather than the middle of the ocean."""
    pts = [p for f in feats for p in f["polygon"]]
    if pts:
        return (sum(p[1] for p in pts) / len(pts),
                sum(p[0] for p in pts) / len(pts))
    return (14.42, 33.12)


def _handle_drawings(state, ar):
    """Turn whatever was drawn into fields the engine can read."""
    drawn = FM.drawings_to_fields(state)
    n = len(drawn["features"])
    if not n and not drawn["rejected"]:
        return

    ui.section(ui.t("draw_here", ar), "", ar)
    for r in drawn["rejected"]:
        ui.note(f"{ui.t('rejected', ar)} #{r['index']}: {r['reason']}",
                "warn", ar)
    if not n:
        return

    total_ha = sum(f["properties"]["area_ha"] for f in drawn["features"])
    ui.stats([(ui.t("drawn_count", ar).format(n=n), f"{total_ha:.1f} ha", None)])

    c1, c2 = st.columns([2, 3])
    out = c1.text_input("GeoJSON", "my_fields.geojson",
                        label_visibility="collapsed")
    if c2.button(ui.t("save_fields", ar), type="primary"):
        written = FM.save_fields(drawn, out)
        st.success(f"{ui.t('saved_to', ar)} {out} — {written}")
        st.caption(ui.t("then_run", ar))
        st.code(f"python src/farm_cli.py --fields {out} "
                f"--season 2022 --crop sorghum --out farm_report.json",
                language="bash")


# ==============================================================================
# FIELD DETAIL
# ==============================================================================

def _render_field(rec, ar):
    left, right = st.columns([3, 2], gap="large")
    rows = D.variables_table(rec, ar)

    with left:
        ui.section(ui.t("all_variables", ar), "", ar)

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
        else:
            # The reason a row is unavailable rides on the row itself, as a
            # tooltip, rather than in a paragraph underneath. It is the one
            # piece of method text that changes what the reader does - it says
            # whether to wait for a clear scene or to go and look - so it stays
            # at the point of use and costs no vertical space.
            ui.variables_table(rows, ar)

        series = rec.get("series") or {}
        if series.get("status") == "OK" and series.get("dates"):
            ui.section(ui.t("through_season", ar), "", ar)
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
        # What the advisory declined to say, and why. This is not method text:
        # it is the absence of advice, and a reader who cannot see the absence
        # will read silence as "nothing to do".
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


if __name__ == "__main__":
    main()
