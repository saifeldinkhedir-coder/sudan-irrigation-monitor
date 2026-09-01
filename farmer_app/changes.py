"""
"What changed since last time" - the page that makes this a monitor.

Every report before this was a season summary. You could read one and know how
the farm stood; you could not read two and know what had moved. That is the
difference between a report and a monitor, and it is the difference between a
tool somebody opens once and a tool somebody opens on Sunday mornings.

The verdict this page exists to get right is that a decline past the NDVI peak
is a crop ripening, not a crop failing. See src/change.py.
"""

from __future__ import annotations

import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import change as CH

import ui
import view as D


VERDICT = {
    "DECLINED": ("تراجع", "declined", "attention"),
    "EXPECTED SENESCENCE": ("نضج متوقّع", "ripening as expected", "ok"),
    "IMPROVED": ("تحسّن", "improved", "ok"),
    "STEADY": ("ثابت", "steady", "ok"),
    "NOT COMPARABLE": ("غير قابل للمقارنة", "not comparable", "unmeasured"),
}


def render(current: dict, ar: bool = False) -> None:
    ui.section(ui.t("changes_title", ar), "", ar)

    prev_path = st.text_input(ui.t("previous_report", ar), "",
                              placeholder="farm_report_2022-10-01.json")
    if not prev_path:
        ui.note(ui.t("changes_how", ar), "", ar)
        return
    if not os.path.exists(prev_path):
        ui.note(ui.t("no_report", ar) + f" <code>{prev_path}</code>", "stop", ar)
        return

    with open(prev_path, encoding="utf-8") as fh:
        previous = json.load(fh)

    cmp = CH.compare(previous, current)
    ui.note(CH.headline(cmp, ar), "", ar)

    counts = cmp["counts"]
    ui.stats([
        (ui.t("v_declined", ar), counts.get("DECLINED", 0), None),
        (ui.t("v_improved", ar), counts.get("IMPROVED", 0), None),
        (ui.t("v_senescence", ar), counts.get("EXPECTED SENESCENCE", 0), None),
        (ui.t("v_incomparable", ar), counts.get("NOT COMPARABLE", 0), None),
    ])

    # Crossing a threshold is a different event from moving, and it is the one
    # that changes what a farmer does today.
    if cmp["crossings"]:
        ui.section(ui.t("crossings", ar), "", ar)
        for c in cmp["crossings"]:
            frm = D.label(D.STATUS_LABEL, c["from"], ar)
            to = D.label(D.STATUS_LABEL, c["to"], ar)
            ui.note(f"<b>{c['name']}</b>: {frm} → {to}",
                    "warn" if c["to"] == "attention" else "", ar)

    ui.section(ui.t("field_by_field", ar), "", ar)
    for c in cmp["changes"]:
        ar_lbl, en_lbl, status = VERDICT.get(c["verdict"],
                                             ("—", "—", "unmeasured"))
        tags = []
        if c.get("gap_days") is not None:
            tags.append(f'{c["from_date"]} → {c["to_date"]}'
                        f' · {c["gap_days"]} {ui.t("days", ar)}')
        if c.get("threshold") is not None:
            tags.append(f'± {c["threshold"]}')
        delta = ("—" if c.get("delta") is None
                 else f'{c["delta"]:+.3f} NDVI')
        sub = (c.get("reason_ar" if ar else "reason")
               or (c.get("judged_against_ar") if ar
                   else c.get("judged_against")) or "")
        ui.field_row(c["name"], status, ar_lbl if ar else en_lbl, tags,
                     right=delta, sub=sub, ar=ar)

    if cmp["new_fields"] or cmp["dropped_fields"]:
        ui.section(ui.t("appeared_vanished", ar), "", ar)
        # A field that disappears from a report is either a boundary somebody
        # removed or a run that failed, and both are worth seeing.
        if cmp["new_fields"]:
            ui.note(ui.t("new_fields", ar) + ", ".join(cmp["new_fields"]),
                    "", ar)
        if cmp["dropped_fields"]:
            ui.note(ui.t("gone_fields", ar) + ", ".join(cmp["dropped_fields"]),
                    "warn", ar)

    # The two refusals this page rests on, inline: they change how every row
    # above is read, which is the test for staying on the working screen.
    ui.note(cmp["note_ar"] if ar else cmp["note"], "", ar)
