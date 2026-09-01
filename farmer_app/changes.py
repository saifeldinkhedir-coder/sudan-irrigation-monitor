"""
"What changed since last time" - the page that makes this a monitor.

Every report before this was a season summary. You could read one and know how
the farm stood; you could not read two and know what had moved.

WHAT CHANGED IN THIS PAGE ITSELF
--------------------------------
It used to ask the reader to TYPE THE PATH of an older report. With a run store
behind it, the honest comparison - the previous run over the SAME farm - is
simply what happens when nobody chooses anything. That is the point of having a
history: it makes the right comparison the default and the wrong one the effort.

THE VERDICT THIS PAGE EXISTS TO GET RIGHT
-----------------------------------------
A decline past the NDVI peak is a crop ripening, not a crop failing. See
src/change.py.
"""

from __future__ import annotations

import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import change as CH
import runs as RUNS

import ui
import view as D


VERDICT = {
    "DECLINED": ("تراجع", "declined", "attention"),
    "EXPECTED SENESCENCE": ("نضج متوقّع", "ripening as expected", "ok"),
    "IMPROVED": ("تحسّن", "improved", "ok"),
    "STEADY": ("ثابت", "steady", "ok"),
    "NOT COMPARABLE": ("غير قابل للمقارنة", "not comparable", "unmeasured"),
}


def render(current: dict, ar: bool = False, farm: str = "",
           runs_root: str = "runs") -> None:
    ui.section(ui.t("changes_title", ar), "", ar)

    store = RUNS.RunStore(runs_root)
    history = store.runs(farm) if farm else []
    previous = None

    if len(history) >= 2:
        # The default is the run before the latest. A reader who wants a
        # different one can pick it; a reader who picks nothing gets the
        # honest comparison rather than none.
        labels = {r["id"]: f'{r["id"]}  ·  {r.get("n_fields", "?")} '
                           f'{ui.t("fields", ar)}' for r in history[:-1]}
        chosen = st.selectbox(ui.t("previous_report", ar),
                              list(labels)[::-1],
                              format_func=lambda k: labels[k])
        previous = store.load(farm, chosen)
        pair_a = next(r for r in history if r["id"] == chosen)
        check = store.comparable(pair_a, history[-1])
        if not check["ok"]:
            ui.note(check["reason_ar"] if ar else check["reason"], "stop", ar)
            return
        if check.get("boundaries_changed"):
            # Not a reason to refuse - fields do get redrawn - but a "change"
            # in a redrawn field is partly the redrawing.
            ui.note(("تغيّر ملف الحدود بين التشغيلين، فجزء من أي تغيّر هنا هو "
                     "إعادة الرسم نفسها." if ar else
                     "The boundary file changed between these runs, so part of "
                     "any change here is the redrawing itself."), "warn", ar)
    else:
        # No history yet: fall back to a path, and say why the page is asking.
        ui.note(("لا سجلّ تشغيلات لهذه المزرعة بعد. شغّل المحرّك من صفحة "
                 "«تشغيل التحليل» مرّتين وستصير المقارنة تلقائية — أو أشر إلى "
                 "تقرير أقدم يدويًّا." if ar else
                 "No run history for this farm yet. Run the engine twice from "
                 "\"Run the analysis\" and the comparison becomes automatic - "
                 "or point at an older report by hand."), "", ar)
        manual = st.text_input(ui.t("previous_report", ar), "",
                               placeholder="farm_report_2022-10-01.json")
        if not manual:
            return
        if not os.path.exists(manual):
            ui.note(ui.t("no_report", ar) + f" <code>{manual}</code>",
                    "stop", ar)
            return
        with open(manual, encoding="utf-8") as fh:
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
    # that changes what somebody does today.
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
        delta = "—" if c.get("delta") is None else f'{c["delta"]:+.3f} NDVI'
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

    # The two refusals this page rests on stay inline: they change how every
    # row above is read, which is the test for staying on the working screen.
    ui.note(cmp["note_ar"] if ar else cmp["note"], "", ar)
