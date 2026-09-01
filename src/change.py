"""
What changed since the last run.

WHY THIS IS THE FEATURE THAT MAKES IT A MONITORING TOOL
-------------------------------------------------------
Every report this platform produced was a season summary. You could read one
and know how the farm stood; you could not read two and know what had moved.
That is the difference between a report and a monitor, and it is the difference
between a tool somebody opens once and a tool somebody opens on Sunday
mornings.

THE MISTAKE THIS MODULE REFUSES TO MAKE
---------------------------------------
NDVI going down is not bad news. A sorghum crop that greened up in August peaks
in October and then senesces on purpose: the canopy yellows, the grain fills,
and NDVI falls all the way to harvest. A change detector that flags every
decline will flag every field on the scheme every autumn, and a tool that cries
wolf at the whole farm at once is worse than no tool, because it also buries
the one field that IS failing.

So a decline is read against the crop's own phenology. Past the NDVI peak, a
fall is EXPECTED SENESCENCE and is reported as ripening, not as damage. Before
the peak, the same fall is a DECLINE. The same number, two verdicts, and the
thing that separates them is the green-up date the engine already computed.

WHAT COUNTS AS A CHANGE AT ALL
------------------------------
A difference smaller than the field's own pixel-to-pixel spread is not a
change; it is the same field measured twice. The test is against the field's
robust sigma, the same statistic the rest of this platform uses, so a noisy
field needs a bigger move to be believed than a uniform one. Where no spread is
recorded, a floor is used and the output says the comparison rested on the
floor rather than on the field.

DATES ARE SCENE DATES, NOT RUN DATES
------------------------------------
Two runs a week apart can rest on satellite scenes a month apart, because the
newer run may have found nothing but cloud. Reporting "7 days" for a 31-day
gap would make a slow drift look like a collapse. Every comparison carries the
dates of the observations it actually rests on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import crops as C


# A move smaller than this is not believed even on a field with no recorded
# spread. Sentinel-2 surface reflectance noise puts NDVI repeatability at
# roughly this order over a stable target.
NDVI_FLOOR = 0.03
SIGMA_K = 1.0            # robust sigmas of the field's own spread

VERDICTS = ("DECLINED", "EXPECTED SENESCENCE", "IMPROVED", "STEADY",
            "NOT COMPARABLE")


def _vigour(rec: dict) -> Optional[float]:
    v = ((rec or {}).get("crop_health") or {}).get("readings", {}).get(
        "vigour", {})
    return v.get("value") if v.get("status") == "OK" else None


def _spread(rec: dict) -> Optional[float]:
    """The field's own robust sigma, if the engine recorded a distribution."""
    v = ((rec or {}).get("crop_health") or {}).get("readings", {}).get(
        "vigour", {})
    for key in ("robust_sigma", "sigma"):
        if v.get(key):
            return float(v[key])
    d = (rec or {}).get("distribution") or {}
    if d.get("p16") is not None and d.get("p84") is not None:
        return (float(d["p84"]) - float(d["p16"])) / 2.0
    return None


def _last_scene(rec: dict) -> Optional[str]:
    dates = ((rec or {}).get("series") or {}).get("dates") or []
    return str(dates[-1])[:10] if dates else None


def _days_between(a: Optional[str], b: Optional[str]) -> Optional[int]:
    if not a or not b:
        return None
    try:
        return (datetime.strptime(b, "%Y-%m-%d")
                - datetime.strptime(a, "%Y-%m-%d")).days
    except ValueError:
        return None


def _past_peak(rec: dict) -> Optional[bool]:
    """Is this field past the peak of its own NDVI curve?

    Read from the phenology the engine already computed. None where phenology
    could not be derived - and None must not be treated as False, because
    assuming a field is still growing is exactly how ripening gets reported as
    failure.
    """
    ph = (rec or {}).get("phenology") or {}
    if ph.get("status") != "OK":
        return None
    peak = ph.get("peak_day")
    series = (rec or {}).get("series") or {}
    days = series.get("day_offsets") or []
    if peak is None:
        return None
    if days:
        return float(days[-1]) > float(peak)
    # Fall back to the season length the curve implied: if the field has a
    # green-up and a season length, and the last scene is beyond green-up plus
    # the distance to the peak, it is past peak.
    gu, length = ph.get("greenup_day"), ph.get("season_length_days")
    if gu is None or length is None:
        return None
    return float(peak) < float(gu) + float(length) / 2.0


def field_change(prev: dict, curr: dict, k: float = SIGMA_K,
                 floor: float = NDVI_FLOOR) -> dict:
    """
    How one field moved between two runs.

    Returns a verdict, the size of the move, what it was judged against, and
    the dates of the scenes it actually rests on.
    """
    name = (curr or {}).get("name") or (prev or {}).get("name")
    v0, v1 = _vigour(prev), _vigour(curr)
    from_date, to_date = _last_scene(prev), _last_scene(curr)
    gap = _days_between(from_date, to_date)

    base = {"name": name, "from": v0, "to": v1,
            "from_date": from_date, "to_date": to_date, "gap_days": gap}

    if v0 is None or v1 is None:
        missing = ("the earlier run" if v0 is None else "the later run")
        return {**base, "verdict": "NOT COMPARABLE", "delta": None,
                "reason": f"no usable vigour reading in {missing}",
                "reason_ar": ("لا قراءة نموّ صالحة في "
                              + ("التشغيل الأسبق" if v0 is None
                                 else "التشغيل الأحدث")),
                # An unmeasured field is not a steady one. It is the state the
                # rest of this platform refuses to collapse into "fine".
                "significant": False}

    delta = round(v1 - v0, 4)
    sigma = _spread(curr) or _spread(prev)
    threshold = max(float(floor), k * sigma) if sigma else float(floor)
    significant = abs(delta) >= threshold

    if not significant:
        verdict = "STEADY"
    elif delta > 0:
        verdict = "IMPROVED"
    else:
        # The whole point of the module. A fall past the peak is the crop
        # ripening; the same fall before the peak is a problem.
        past = _past_peak(curr)
        verdict = "EXPECTED SENESCENCE" if past else "DECLINED"

    return {
        **base, "delta": delta, "verdict": verdict,
        "significant": significant,
        "threshold": round(threshold, 4),
        "judged_against": ("this field's own spread" if sigma
                           else "the fixed noise floor, because no spread was "
                                "recorded for this field"),
        "judged_against_ar": ("تشتّت هذا الحقل نفسه" if sigma
                              else "أرضية الضجيج الثابتة، إذ لم يُسجَّل تشتّت "
                                   "لهذا الحقل"),
        "past_peak": _past_peak(curr),
    }


def _status_of(rec: dict) -> str:
    """The four-state classification, recomputed here so a comparison does not
    depend on the display layer. Kept deliberately simple: this asks only
    whether the field crossed its own threshold, not where it sits within the
    farm, because a within-farm rank moves when OTHER fields move and that is
    not a change in this field."""
    v = ((rec or {}).get("crop_health") or {}).get("readings", {}).get(
        "vigour", {})
    if v.get("status") != "OK" or v.get("value") is None:
        return "unmeasured"
    thr = v.get("threshold")
    if thr is not None and v["value"] < thr:
        return "attention"
    return "ok"


def compare(previous: dict, current: dict, k: float = SIGMA_K) -> dict:
    """
    Two reports, field by field.

    Fields present in only one run are listed separately rather than silently
    dropped or treated as new. A field that disappears from a report is either
    a boundary that was removed or a run that failed, and both are worth
    seeing.
    """
    prev_by = {f.get("name"): f for f in (previous or {}).get("fields", [])}
    curr_by = {f.get("name"): f for f in (current or {}).get("fields", [])}

    changes, crossings = [], []
    for name, rec in curr_by.items():
        if name not in prev_by:
            continue
        ch = field_change(prev_by[name], rec, k=k)
        s0, s1 = _status_of(prev_by[name]), _status_of(rec)
        ch["status_from"], ch["status_to"] = s0, s1
        ch["crossed"] = s0 != s1
        changes.append(ch)
        if s0 != s1:
            crossings.append({"name": name, "from": s0, "to": s1})

    order = {v: i for i, v in enumerate(VERDICTS)}
    changes.sort(key=lambda c: (order.get(c["verdict"], 9),
                                c.get("delta") if c.get("delta") is not None
                                else 0))

    new = sorted(set(curr_by) - set(prev_by))
    gone = sorted(set(prev_by) - set(curr_by))
    counts = {v: sum(1 for c in changes if c["verdict"] == v) for v in VERDICTS}

    return {
        "changes": changes,
        "counts": counts,
        "crossings": crossings,
        "new_fields": new,
        "dropped_fields": gone,
        "n_compared": len(changes),
        "previous_generated": (previous or {}).get("generated_utc"),
        "current_generated": (current or {}).get("generated_utc"),
        "crop": C.resolve((current or {}).get("crop")),
        "note": ("Declines past the NDVI peak are reported as ripening, not "
                 "as damage. A change smaller than the field's own spread is "
                 "reported as steady. Dates are the dates of the satellite "
                 "scenes the two runs rest on, not the dates the runs were "
                 "started."),
        "note_ar": ("الهبوط بعد ذروة NDVI يُقرأ نضجًا لا ضررًا. والتغيّر الأصغر "
                    "من تشتّت الحقل نفسه يُقرأ ثباتًا. والتواريخ تواريخ مشاهد "
                    "الأقمار التي يقوم عليها التشغيلان، لا تواريخ تشغيلهما."),
    }


def headline(cmp: dict, ar: bool = False) -> str:
    """One sentence for the top of the page."""
    c = cmp.get("counts", {})
    n = cmp.get("n_compared", 0)
    if not n:
        return ("لا حقل مشترك بين التشغيلين، فلا مقارنة." if ar
                else "No field is present in both runs, so there is nothing "
                     "to compare.")
    dec, imp = c.get("DECLINED", 0), c.get("IMPROVED", 0)
    if ar:
        return (f"من {n} حقلًا: {dec} تراجعت قبل الذروة، {imp} تحسّنت، "
                f"{c.get('STEADY', 0)} ثابتة، "
                f"{c.get('EXPECTED SENESCENCE', 0)} في نضج متوقّع.")
    return (f"Of {n} fields: {dec} declined before peak, {imp} improved, "
            f"{c.get('STEADY', 0)} steady, "
            f"{c.get('EXPECTED SENESCENCE', 0)} ripening as expected.")
