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
import auth as AUTH
import onboarding as ONB
import search as S
import changes as CG
import runner as RUN
import ui
import fieldmap as FM
import crops as CROPS
import registry as REG
import runs as RUNS
import report_html as RH
import backup as BK
import nutrition_climate_ground as NCG


def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--report", default="farm_report.json")
    p.add_argument("--fields", default=None)
    p.add_argument("--farm", default="farm",
                   help="the name this farm is filed under in the run store")
    p.add_argument("--hierarchy", default="flat",
                   help="gezira | flat - see src/registry.py, and confirm the "
                        "level names with the scheme before using them")
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

    # The gate comes before anything is read off disk. Its "this deployment is
    # OPEN" warning used to sit in the sidebar of every screen. That warning is
    # for whoever DEPLOYS this, and they read it once; putting it in front of a
    # farmer every session is shouting a deployment decision at the person who
    # did not make it. It now goes to the console at startup and onto the About
    # page, which is where deployment questions are answered.
    user = AUTH.gate(ar=ar, quiet=True)

    # Farm name and file paths are operator configuration, not daily use. They
    # arrive as command-line arguments and are editable under Tools. A sidebar
    # whose first controls are two file paths is a program's sidebar, not a
    # product's.
    farm = st.session_state.get("_farm", args.farm)
    report_path = st.session_state.get("_report", args.report)
    fields_path = st.session_state.get("_fields", args.fields or "")

    if not AUTH.may_see(user, farm):
        ui.topbar(ar)
        ui.note(ui.t("not_your_farm", ar), "stop", ar)
        st.stop()

    # THE FIRST SCREEN. The app used to open on a demonstration farm: not the
    # reader's land, looking like a working product, with nothing on it saying
    # how to reach their own fields.
    if ONB.needed(report_path, fields_path):
        ui.topbar(ar)
        choice = ONB.render(ar)
        if choice.get("mode") == "demo":
            st.session_state["_report"] = choice["report"]
            st.session_state["_fields"] = choice["fields"]
            st.rerun()
        elif choice.get("mode") == "load":
            st.session_state["_fields"] = choice["fields"]
            st.rerun()
        elif choice.get("mode") == "draw":
            st.session_state["_draw"] = True
            st.rerun()
        if not st.session_state.get("_draw"):
            st.stop()

    if not os.path.exists(report_path):
        # Boundaries but no analysis yet: the next step is a run, not a lecture.
        ui.topbar(ar)
        ui.note(ui.t("no_report", ar), "warn", ar)
        season_year = 2022
        fc = (_load(fields_path)
              if fields_path and os.path.exists(fields_path) else None)
        RUN.panel(fields_path if fc else None,
                  len((fc or {}).get("features", [])), season_year,
                  "default", ar)
        if not fc:
            _render_map({"fields": []}, None, ar)
        st.stop()

    report = _load(report_path)
    season = report.get("season", {})

    # The season, crop and field-count chips are gone. They said what the row
    # of figures immediately below already says, and what every field row says
    # again - three restatements above the thing the reader came for.
    #
    # The demonstration pill is the one thing left, and it is not decoration:
    # real imagery over invented boundaries is the most misleading combination
    # this tool can produce, and it has to be labelled where a reader will see
    # it. One pill is not a row of chips.
    ui.topbar(ar, demo=bool(report.get("note")))

    field_fc = (_load(fields_path)
                if fields_path and os.path.exists(fields_path) else None)
    page = _navigation(ar, farm)

    if page == "record":
        R.render(report)
        return
    if page == "about":
        A.render(report, ar)
        return
    if page == "changes":
        CG.render(report, ar, farm=farm)
        return
    if page == "units":
        _render_units(report, field_fc, ar)
        return
    if page == "backup":
        _render_backup(ar)
        return
    if page == "run":
        season_year = int(str(season.get("start", "2022"))[:4] or 2022)
        produced = RUN.panel(fields_path if field_fc else None,
                             len((field_fc or {}).get("features", [])),
                             season_year, CROPS.resolve(report.get("crop")), ar)
        if produced:
            # A finished run goes straight into the history, so the comparison
            # on the next run is automatic rather than something the reader has
            # to arrange.
            try:
                entry = RUNS.RunStore().record(farm, produced,
                                               fields_path=fields_path)
                st.success(f'{ui.t("recorded_as", ar)} {entry["id"]}')
            except Exception as e:                       # noqa: BLE001
                st.warning(f'{ui.t("not_recorded", ar)} {e}')
            st.session_state["_report"] = produced
        return

    index = S.field_index(report, field_fc, ar=ar)

    # The compact view is the whole fields page for somebody on a phone: which
    # field, and what to do. No map and no drawing, because neither works with
    # a thumb on a 5-inch screen in daylight. It is a view mode, so it lives
    # with the other switches under Tools rather than on the sidebar floor.
    if st.session_state.get("_compact"):
        _render_compact(report, index, ar)
        return

    _render_accuracy(ar)

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
    _render_export(report, field_fc, ar)

    ui.section(f'{ui.t("field_detail", ar)} — {chosen}', "", ar)
    _render_crop(rec, ar)
    _render_field(rec, ar)
    _render_disease(rec, ar)
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

    # THE FIELD EDITOR. A boundary with no name, no crop and no sowing date is
    # a shape, not a field: the search cannot find it, the engine gives it the
    # run's crop whatever is standing in it, and the report calls it "حقل 3".
    # This is where a drawn shape becomes a record.
    ui.section(ui.t("name_your_fields", ar), ui.t("editor_help", ar), ar)
    crop_keys = [k for k, _l in CROPS.names(ar)]
    edited = st.data_editor(
        [{ui.t("col_name", ar): f["properties"]["name"],
          ui.t("col_crop", ar): CROPS.label(
              f["properties"].get("crop") or "default", ar),
          ui.t("col_sown", ar): str(f["properties"].get("sowing_date", "")),
          ui.t("col_tenancy", ar): str(f["properties"].get("tenancy", "")),
          ui.t("col_area", ar): float(f["properties"].get("area_ha") or 0.0)}
         for f in drawn["features"]],
        width="stretch", hide_index=True, key="field_editor",
        column_config={
            ui.t("col_crop", ar): st.column_config.SelectboxColumn(
                options=[CROPS.label(k, ar) for k in crop_keys]),
            ui.t("col_area", ar): st.column_config.NumberColumn(disabled=True),
        })

    by_label = {CROPS.label(k, ar): k for k in crop_keys}
    for feat, row in zip(drawn["features"], edited):
        props = feat["properties"]
        props["name"] = (row.get(ui.t("col_name", ar))
                         or props["name"]).strip()
        crop_key = by_label.get(row.get(ui.t("col_crop", ar)), "default")
        if crop_key != "default":
            props["crop"] = crop_key
        else:
            props.pop("crop", None)
        for key, col in (("sowing_date", "col_sown"), ("tenancy", "col_tenancy")):
            val = str(row.get(ui.t(col, ar)) or "").strip()
            if val:
                props[key] = val
            else:
                props.pop(key, None)

    c1, c2 = st.columns([2, 3])
    out = c1.text_input("GeoJSON", "my_fields.geojson",
                        label_visibility="collapsed")
    if c2.button(ui.t("save_fields", ar), type="primary"):
        written = FM.save_fields(drawn, out)
        st.success(f"{ui.t('saved_to', ar)} {out} — {written}")
        st.caption(ui.t("then_run", ar))
        st.info(ui.t("run_from_app", ar))


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
            # Every cell as text. Arrow types a column from its first value,
            # so one column holding "0.2188" and then the integer 80 dies with
            # `Expected bytes, got a 'int' object`. These are readings with
            # their units, not quantities to sort arithmetically.
            st.dataframe(
                [{ui.t("var", ar): str(r["variable"]),
                  ui.t("value", ar): str(r["value"]),
                  ui.t("compared", ar): str(r["threshold"]),
                  ui.t("reading", ar): str(r["verdict"]),
                  ui.t("sensor", ar): str(r["sensor"]),
                  ui.t("measured_at", ar): str(r["scale"])} for r in rows],
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


# The product is ONE screen: the map, the list, and the field you picked. That
# is what a farm-monitoring tool is, and it is what this app was for.
#
# Then eleven commissioned features arrived, and each one honestly needed
# somewhere to live, and the sidebar became a seven-item menu in which the
# product was the first item. Nothing was wrong with any single addition; the
# sum was wrong. A person opening the app met an administration console and had
# to find the farm inside it.
#
# So the capability stays and the SHAPE goes back. Two views are the product -
# the fields, and what changed since last time. Everything else is a tool: real,
# reachable in one click, and not competing with the thing the app is for.
MAIN_PAGES = [("fields", "page_fields"), ("changes", "page_changes")]
TOOL_PAGES = [("run", "page_run"), ("record", "page_record"),
              ("units", "page_units"), ("backup", "page_backup"),
              ("about", "page_about")]


def _navigation(ar, farm: str = "") -> str:
    """
    The sidebar. Returns the page key; defaults to the product.

    "What changed" only appears once there is something to compare. It used to
    be a permanent second item that, with one run or none, could say nothing
    but "no history yet" and offer a box to type a file path into. A navigation
    item whose only possible content is its own empty state is not navigation;
    it is a promise the sidebar cannot keep, and the reader pays for it with a
    click every time.
    """
    can_compare = False
    try:
        can_compare = len(RUNS.RunStore().runs(farm)) >= 2
    except Exception:                                    # noqa: BLE001
        can_compare = False

    if can_compare:
        def _picked():
            st.session_state["_page"] = st.session_state["_main"]

        keys = [k for k, _l in MAIN_PAGES]
        current = st.session_state.get("_page", "fields")
        st.sidebar.radio(
            ui.t("page", ar), keys,
            index=keys.index(current) if current in keys else 0,
            format_func=lambda k: ui.t(dict(MAIN_PAGES)[k], ar),
            key="_main", on_change=_picked)
    elif st.session_state.get("_page") == "changes":
        st.session_state["_page"] = "fields"

    with st.sidebar.expander(ui.t("tools", ar)):
        for key, label in TOOL_PAGES:
            if st.button(ui.t(label, ar), key=f"nav_{key}", width="stretch"):
                st.session_state["_page"] = key
        st.toggle(ui.t("compact", ar), key="_compact",
                  help=ui.t("compact_help", ar))
        st.divider()
        _sources(ar)

    page = st.session_state.get("_page", "fields")
    if page in dict(TOOL_PAGES):
        # A tool is somewhere you went, so there is a way back.
        if st.sidebar.button(ui.t("back_to_fields", ar), width="stretch",
                             type="primary"):
            st.session_state["_page"] = "fields"
            st.rerun()
    return page


def _sources(ar) -> None:
    """Where the data comes from - operator configuration, under Tools.

    These were the first three controls in the sidebar, above everything the
    app is for. They are set once, usually on the command line, and a farmer
    never touches them.
    """
    args = _args()
    st.caption(ui.t("sources", ar))
    farm = st.text_input(ui.t("farm_name", ar),
                         st.session_state.get("_farm", args.farm))
    report = st.text_input("Farm report JSON",
                           st.session_state.get("_report", args.report))
    fields = st.text_input("Field polygons GeoJSON",
                           st.session_state.get("_fields", args.fields or ""))
    if (farm, report, fields) != (st.session_state.get("_farm", args.farm),
                                  st.session_state.get("_report", args.report),
                                  st.session_state.get("_fields",
                                                       args.fields or "")):
        st.session_state["_farm"] = farm
        st.session_state["_report"] = report
        st.session_state["_fields"] = fields
        st.rerun()


def _render_units(report, field_fc, ar):
    """
    The farm rolled up to an administrative level.

    This is the question anybody with authority over more than one field asks
    first, and a flat list cannot answer it without a spreadsheet.
    """
    args = _args()
    h = REG.preset(st.sidebar.selectbox(
        ui.t("hierarchy", ar), list(REG.PRESETS),
        index=list(REG.PRESETS).index(args.hierarchy)
        if args.hierarchy in REG.PRESETS else 0))
    ui.section(ui.t("page_units", ar), h.name, ar)

    if h.depth() == 0:
        ui.note(("لا هرم إداري في هذا التشغيل. اختر «gezira» إن كانت حقولك "
                 "تحمل المجموعة والقسم والنمرة والحواشة." if ar else
                 "This deployment has no hierarchy. Choose \"gezira\" if your "
                 "fields carry group, block, number and tenancy."), "", ar)
        return

    props_by = {(f.get("properties") or {}).get("name", ""):
                (f.get("properties") or {})
                for f in (field_fc or {}).get("features", [])}
    level = st.selectbox(ui.t("roll_up_to", ar), h.keys,
                         format_func=lambda k: h.label(k, ar))
    agg = REG.aggregate(report, h, level, props_by)

    if not agg["units"] and not agg["unplaced"]:
        ui.note(ui.t("no_match", ar), "warn", ar)
        return

    for u in agg["units"]:
        tags = [f'{u["n_fields"]} {ui.t("fields", ar)}',
                f'{ui.t("coverage", ar)} {u["coverage"]:.0%}']
        if u["n_unmeasured"]:
            tags.append(f'{u["n_unmeasured"]} {D.label(D.STATUS_LABEL, "unmeasured", ar)}')
        status = ("attention" if u["n_attention"] else
                  "unmeasured" if u["withheld"] else "ok")
        right = (ui.t("unit_withheld", ar) if u["withheld"]
                 else f'NDVI {u["mean_vigour"]:.3f}')
        # A withheld mean says WHY, in place of the number. Forty fields of
        # which six could not be seen produce a figure describing thirty-four.
        ui.field_row(u["key"], status,
                     f'{u["n_attention"]} {D.label(D.STATUS_LABEL, "attention", ar)}',
                     tags, right=right,
                     sub=(u.get("reason_ar") if ar else u.get("reason", "")),
                     ar=ar)

    if agg["unplaced"]:
        ui.section(ui.t("unplaced_fields", ar), "", ar)
        for u in agg["unplaced"]:
            st.caption(f'{u["name"]} — {u["reason"]}')

    with st.expander(ui.t("why_q", ar)):
        st.caption(agg["basis"])


def _render_backup(ar):
    """What is lost if this machine is, and one file that carries it away."""
    ui.section(ui.t("page_backup", ar), "", ar)
    s = BK.survey(".")
    ui.note(s["note_ar"] if ar else s["note"], "", ar)

    rows = []
    for f in s["found"]:
        n = sum(v for v in (f.get("rows") or {}).values()
                if isinstance(v, int))
        rows.append((f["file"], f'{n} rows', f'{f["bytes"] // 1024} KB'))
    ui.stats([(ui.t("backup_what", ar), len(s["found"]), None),
              (ui.t("photographs", ar), s["n_photographs"],
               f'{s["photograph_bytes"] // 1024} KB')])
    for name, n, size in rows:
        ui.field_row(name, "ok", n, [size], ar=ar)
    for m in s["missing"]:
        ui.field_row(m["file"], "unmeasured", "—", [], sub=m["why"], ar=ar)

    dest = st.text_input("ZIP", "farm_backup.zip")
    if st.button(ui.t("backup_make", ar), type="primary"):
        made = BK.create(dest)
        st.success(f'{ui.t("backup_done", ar)}: {dest} '
                   f'({made["bytes"] // 1024} KB, {made["n_files"]})')
        # Verified immediately. An untested backup is a belief, not a backup.
        v = BK.verify(dest)
        if v["ok"]:
            st.caption(f'✓ {v["n_files"]}')
        else:
            st.error(v["reason"])
        ui.note(made["warning_ar"] if ar else made["warning"], "warn", ar)

    check = st.text_input(ui.t("backup_verify", ar), "")
    if check:
        v = BK.verify(check)
        (st.success if v["ok"] else st.error)(
            f'{v.get("n_files", 0)} · {v.get("reason") or "ok"}')


def _render_compact(report, index, ar):
    """The phone view: which field, and what to do.

    No map and no drawing - neither works with a thumb on a five-inch screen in
    daylight, and the officer standing in a field does not need them. They need
    the order and the reason.
    """
    ranked = sorted(index, key=lambda r: (STATUS_RANK.get(r["status"], 9),
                                          r["vigour"] if r["vigour"] is not None
                                          else 9))
    counts = S.status_counts(index)
    ui.stats([(D.label(D.STATUS_LABEL, k, ar), counts[k], None)
              for k in ("attention", "watch", "unmeasured")])
    for r in ranked:
        rec = next((f for f in report.get("fields", [])
                    if f.get("name") == r["name"]), {})
        adv = rec.get("advisory" if ar else "advisory_en") or {}
        first = (adv.get("items") or [{}])[0].get("text", "")
        ui.field_row(r["name"], r["status"],
                     D.label(D.STATUS_LABEL, r["status"], ar),
                     [r["crop"]] if r.get("crop") else [],
                     right=("NDVI %.3f" % r["vigour"])
                     if r["vigour"] is not None else "—",
                     sub=first or r["why"], ar=ar)


def _render_accuracy(ar):
    """
    The only figure here that MEASURES this tool's accuracy rather than
    claiming it. It is put on the working screen because no competitor shows
    one - not because they are better, but because they do not collect it.
    """
    try:
        store = NCG.ObservationStore("observations.db")
    except Exception:                                    # noqa: BLE001
        return
    try:
        s = store.agreement_summary()
    finally:
        store.close()
    if s.get("available"):
        ui.stats([(ui.t("accuracy", ar),
                   f'{round(100 * s["agreement_rate"])}%',
                   f'{s["total"]} · {s["unclear"]} '
                   + ("غير واضحة" if ar else "unclear"))])
    # Nothing when there is nothing. The invitation to start collecting
    # comparisons belongs on the scouting form, where somebody can act on it -
    # not as a permanent line on the main screen saying that a figure does not
    # exist yet.


def _render_export(report, field_fc, ar):
    """One file with its data and its map inside it."""
    ui.section(ui.t("export", ar), "", ar)
    ui.note(ui.t("export_why", ar), "", ar)
    doc = RH.build(report, field_fc, ar=ar)
    st.download_button(ui.t("export_html", ar), data=doc.encode("utf-8"),
                       file_name="farm_report.html", mime="text/html")


def _render_crop(rec, ar):
    """Which crop this field was analysed as, and where the label came from.

    A crop nobody declared and a crop somebody declared that the library did
    not recognise are different facts, and the second means every crop-specific
    figure on this screen rests on generic parameters."""
    c = D.crop_line(rec, ar)
    if not c["available"]:
        ui.note(c["text"], "warn", ar)
        return
    tags = [c["source_text"]] if c["source_text"] else []
    if c.get("heat_stress_c"):
        tags.append(f'{ui.t("heat_over", ar)} {c["heat_stress_c"]} °C')
    ui.field_row(c["name"], "ok" if c["recognised"] else "unmeasured",
                 ui.t("crop_of_field", ar), tags, ar=ar)
    if not c["recognised"]:
        ui.note(c["warning"] or ui.t("crop_generic", ar), "warn", ar)
    # Silence when the canopy is plausible: a check that speaks when it passes
    # is noise.
    chk = D.crop_check_line(rec, ar)
    if chk:
        ui.note(chk, "warn", ar)


def _render_disease(rec, ar):
    """
    The three-rung disease and pest layer.

    The colours carry the argument. REPORTED is the only red, because it is the
    only rung that names a disease as present. A weather window is grey: it is
    a statement about the sky over every field, healthy ones included, and
    drawing it red would be this whole product category's failure in one
    colour.
    """
    p = D.disease_panel(rec, ar)
    ui.section(ui.t("disease_title", ar), "", ar)
    if not p["available"]:
        ui.note(p["reason"], "warn", ar)
        return

    ui.field_row(p["headline"] or p["level_label"], p["status_key"],
                 p["level_label"],
                 [p["problem_label"]] if p["problem_label"] else [],
                 sub=p["note"], ar=ar)
    if p["next_step"]:
        ui.note(p["next_step"], "", ar)

    left, right = st.columns(2, gap="large")

    with left:
        ui.section(ui.t("anomaly_title", ar), "", ar)
        line = D.anomaly_line(rec, ar)
        ui.note(line or "—", "", ar)

    with right:
        ui.section(ui.t("weather_windows", ar), "", ar)
        if p["risk_reason"]:
            ui.note(p["risk_reason"], "warn", ar)
        for r in p["risks"]:
            kind = "warn" if r["band"] == "FAVOURABLE" else ""
            ui.note(f'<b>{r["name"]}</b> — {r["band_label"]} · '
                    + ui.t("risk_days", ar).format(d=r["days"], n=r["window"]),
                    kind, ar)
            if r["band"] == "FAVOURABLE" and r["scout"]:
                st.caption(f'{ui.t("scout_for", ar)} {r["scout"]}')
        # The absence of a risk line for a migratory pest must read as
        # "nothing here predicts it", never as "it is fine".
        if p["no_model"]:
            ui.note(ui.t("no_weather_model", ar) + " "
                    + "، ".join(n["name"] for n in p["no_model"]), "", ar)

    with st.expander(ui.t("refusal_title", ar)):
        st.caption(p["refusal"])
        st.caption(ui.t("disease_ladder", ar))


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
