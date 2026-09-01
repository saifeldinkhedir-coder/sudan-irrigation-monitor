"""
The operator console - everything that is not the farm screen.

    streamlit run console/app.py -- --farm "Gezira block 14" \
        --report farm_report.json --fields my_fields.geojson

WHY THIS IS A SEPARATE APPLICATION
----------------------------------
The farm screen is one screen: the map, the list, and the field you picked.
That is what a farm-monitoring tool is.

Eleven operator features were commissioned and built, and each one honestly
needed somewhere to live. They went into the farm app's sidebar, which became a
seven-item menu with the farm as item one. Moving them into a collapsed drawer
did not fix it: a drawer full of pages is still pages, and the reader still has
to open it to discover it is not for them. Every session began with a decision
that had nothing to do with their crop.

So the split is by AUDIENCE, not by tidiness:

    farmer_app/   the farm. A farmer or a field officer, every morning.
    console/      the machinery. An operator, occasionally.
    dashboard/    the canal network. A scheme manager.

Nothing was deleted; every page here is the module it always was. What changed
is that a farmer no longer has to walk past it.

WHY THE SPLIT IS ALSO THE RIGHT SECURITY BOUNDARY
-------------------------------------------------
Running the engine, editing the sources, and writing a backup archive are
things that spend money, change files and copy other people's records. They now
sit behind a different address, which is the natural place to put a different
password - or no route at all from wherever the farm screen is published.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "farmer_app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import about as A                      # noqa: E402
import auth as AUTH                    # noqa: E402
import changes as CG                   # noqa: E402
import record as R                     # noqa: E402
import runner as RUN                   # noqa: E402
import ui                              # noqa: E402
import view as D                       # noqa: E402
import crops as CROPS                  # noqa: E402
import backup as BK                    # noqa: E402
import registry as REG                 # noqa: E402
import runs as RUNS                    # noqa: E402


PAGES = [("run", "page_run"), ("changes", "page_changes"),
         ("record", "page_record"), ("units", "page_units"),
         ("backup", "page_backup"), ("about", "page_about")]


def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--report", default="farm_report.json")
    p.add_argument("--fields", default=None)
    p.add_argument("--farm", default="farm")
    p.add_argument("--hierarchy", default="flat",
                   help="gezira | flat - see src/registry.py, and confirm the "
                        "level names with the scheme before using them")
    known, _ = p.parse_known_args()
    return known


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    st.set_page_config(page_title="Farm Monitor console", page_icon="⚙",
                       layout="wide")
    ui.inject()
    args = _args()

    lang = st.sidebar.radio("اللغة · Language", ["العربية", "English"],
                            horizontal=True)
    ar = lang == "العربية"

    # Here the security warning IS for the reader, so it is shown rather than
    # printed: this is the application that runs the engine and writes the
    # archives, and whoever opens it is the person who decides where it is
    # published.
    user = AUTH.gate(ar=ar)

    st.markdown(f'<div class="topbar fm{" rtl" if ar else ""}"'
                f'{" dir=rtl" if ar else ""}>'
                f'<h1>{ui.t("console_title", ar)}</h1>'
                f'<p>{ui.t("console_sub", ar)}</p></div>',
                unsafe_allow_html=True)

    farm = st.sidebar.text_input(ui.t("farm_name", ar), args.farm)
    if not AUTH.may_see(user, farm):
        ui.note(ui.t("not_your_farm", ar), "stop", ar)
        st.stop()

    report_path = st.sidebar.text_input("Farm report JSON",
                                        st.session_state.get("_report",
                                                             args.report))
    fields_path = st.sidebar.text_input(
        "Field polygons GeoJSON", args.fields or "")

    page = st.sidebar.radio(
        ui.t("page", ar), [k for k, _l in PAGES],
        format_func=lambda k: ui.t(dict(PAGES)[k], ar))

    report = _load(report_path) if os.path.exists(report_path) else None
    field_fc = (_load(fields_path)
                if fields_path and os.path.exists(fields_path) else None)

    if page == "run":
        _run(report, fields_path, field_fc, farm, ar)
        return

    if report is None:
        ui.note(ui.t("no_report", ar) + f" <code>{report_path}</code>",
                "stop", ar)
        return

    if page == "changes":
        CG.render(report, ar, farm=farm)
    elif page == "record":
        R.render(report)
    elif page == "units":
        _units(report, field_fc, ar, args.hierarchy)
    elif page == "backup":
        _backup(ar)
    elif page == "about":
        _accuracy(ar)
        A.render(report, ar)


def _run(report, fields_path, field_fc, farm, ar):
    season = (report or {}).get("season", {})
    year = int(str(season.get("start", "2022"))[:4] or 2022)
    produced = RUN.panel(fields_path if field_fc else None,
                         len((field_fc or {}).get("features", [])), year,
                         CROPS.resolve((report or {}).get("crop")), ar)
    if not produced:
        return
    # A finished run goes into the history immediately, so the comparison on
    # the next run is automatic rather than something anybody has to arrange.
    try:
        entry = RUNS.RunStore().record(farm, produced, fields_path=fields_path)
        st.success(f'{ui.t("recorded_as", ar)} {entry["id"]}')
    except Exception as e:                                   # noqa: BLE001
        st.warning(f'{ui.t("not_recorded", ar)} {e}')
    st.session_state["_report"] = produced


def _accuracy(ar):
    """
    How often the satellite agreed with somebody who walked out and looked.

    The only figure in this platform that MEASURES its accuracy rather than
    claiming it - and it belongs here rather than on the farm screen, where it
    was a permanent line announcing that a number did not exist yet.
    """
    import nutrition_climate_ground as NCG
    try:
        store = NCG.ObservationStore("observations.db")
    except Exception:                                        # noqa: BLE001
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
        st.caption(ui.t("accuracy_help", ar))
    else:
        ui.note(ui.t("accuracy_none", ar), "", ar)


def _units(report, field_fc, ar, default_hierarchy):
    """The farm rolled up to an administrative level - the question anybody
    with authority over more than one field asks first."""
    h = REG.preset(st.selectbox(
        ui.t("hierarchy", ar), list(REG.PRESETS),
        index=list(REG.PRESETS).index(default_hierarchy)
        if default_hierarchy in REG.PRESETS else 0))
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

    for u in agg["units"]:
        tags = [f'{u["n_fields"]} {ui.t("fields", ar)}',
                f'{ui.t("coverage", ar)} {u["coverage"]:.0%}']
        if u["n_unmeasured"]:
            tags.append(f'{u["n_unmeasured"]} '
                        f'{D.label(D.STATUS_LABEL, "unmeasured", ar)}')
        status = ("attention" if u["n_attention"] else
                  "unmeasured" if u["withheld"] else "ok")
        right = (ui.t("unit_withheld", ar) if u["withheld"]
                 else f'NDVI {u["mean_vigour"]:.3f}')
        # A withheld mean says WHY in place of the number.
        ui.field_row(u["key"], status,
                     f'{u["n_attention"]} '
                     f'{D.label(D.STATUS_LABEL, "attention", ar)}',
                     tags, right=right,
                     sub=(u.get("reason_ar") if ar else u.get("reason", "")),
                     ar=ar)

    if agg["unplaced"]:
        ui.section(ui.t("unplaced_fields", ar), "", ar)
        for u in agg["unplaced"]:
            st.caption(f'{u["name"]} — {u["reason"]}')
    with st.expander(ui.t("why_q", ar)):
        st.caption(agg["basis"])


def _backup(ar):
    """What is lost if this machine is, and one file that carries it away."""
    ui.section(ui.t("page_backup", ar), "", ar)
    s = BK.survey(".")
    ui.note(s["note_ar"] if ar else s["note"], "", ar)

    ui.stats([(ui.t("backup_what", ar), len(s["found"]), None),
              (ui.t("photographs", ar), s["n_photographs"],
               f'{s["photograph_bytes"] // 1024} KB')])
    for f in s["found"]:
        n = sum(v for v in (f.get("rows") or {}).values()
                if isinstance(v, int))
        ui.field_row(f["file"], "ok", f"{n} rows",
                     [f'{f["bytes"] // 1024} KB'], ar=ar)
    for m in s["missing"]:
        ui.field_row(m["file"], "unmeasured", "—", [], sub=m["why"], ar=ar)

    dest = st.text_input("ZIP", "farm_backup.zip")
    if st.button(ui.t("backup_make", ar), type="primary"):
        made = BK.create(dest)
        st.success(f'{ui.t("backup_done", ar)}: {dest} '
                   f'({made["bytes"] // 1024} KB, {made["n_files"]})')
        # Verified immediately: an untested backup is a belief, not a backup.
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


if __name__ == "__main__":
    main()
