"""
Dashboard data logic - kept separate from the Streamlit view so it can be tested
without a browser or a running server.

Everything here takes the engine's results JSON (a plain dict) and turns it into
the rows, labels and phrasings the manager view renders. The integrity rules
follow the data all the way to the screen: a NOT AVAILABLE indicator becomes the
words "not available", never a 0 or a blank that reads as "fine".
"""

from __future__ import annotations

from typing import Optional


def _fmt(v, nd=3, dash="—"):
    return dash if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def canal_rows(results: dict) -> list:
    """
    One row per canal for the manager table. Each row is display-ready: numbers
    formatted, missing values shown as em-dash, and an explicit `sort_gap` /
    `flagged` the table can order by.

    Rows are the manager phrasing of the same numbers the farmer and researcher
    see differently - one engine, three phrasings.
    """
    rows = []
    for c in results.get("canals", []):
        eq = c.get("head_tail_equity", {})
        cw = c.get("canal_water", {})
        ext = c.get("irrigated_extent", {})

        reliable = eq.get("gap_reliable", True)
        gap = (eq.get("head_tail_gap")
               if eq.get("status") == "OK" and reliable else None)
        ci = eq.get("head_tail_gap_ci95") if reliable else None
        flagged = bool(eq.get("flagged"))
        gap_disp = (_pct(gap) if gap is not None
                    else ("unreliable (low head)"
                          if eq.get("status") == "OK" and not reliable else "—"))

        # extent reliability from the Otsu bimodality note
        ext_note = (ext.get("provenance") or {}).get("notes", "")
        ext_weak = "WEAK SPLIT" in ext_note

        rows.append({
            "name": c.get("name", "?"),
            "office": c.get("command_area_provenance", {}).get("office", ""),
            "flagged": flagged,
            "gap_display": gap_disp,
            "gap_ci_display": (f"{_pct(ci[0])} … {_pct(ci[1])}"
                               if ci and ci[0] is not None else "—"),
            "sort_gap": gap if gap is not None else float("-inf"),
            "equity_status": eq.get("status", "—"),
            "water_status": cw.get("status", "—"),
            "water_display": _fmt(cw.get("value")) if cw.get("status") == "OK" else "not available",
            "extent_display": (_pct(ext.get("value")) if ext.get("status") == "OK"
                               else "not available"),
            "extent_reliable": not ext_weak if ext.get("status") == "OK" else None,
            "command_source": (c.get("command_area_provenance", {})
                               .get("command_area_source", "—").split(":")[0]),
            "rainfall_mm": c.get("seasonal_rainfall_mm"),
            "et_mm": c.get("seasonal_et_mm"),
        })
    return rows


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{100 * v:.0f}%"


def sort_canals(rows: list, by: str = "gap") -> list:
    """Sort for the manager's actual question: which canals to look at first.
    Flagged canals float to the top, then by gap size."""
    if by == "gap":
        return sorted(rows, key=lambda r: (not r["flagged"], -r["sort_gap"]))
    if by == "water":
        order = {"OK": 0, "INSUFFICIENT DATA": 1, "NOT AVAILABLE": 2, "—": 3}
        return sorted(rows, key=lambda r: order.get(r["water_status"], 9))
    if by == "name":
        return sorted(rows, key=lambda r: r["name"])
    return rows


def flagged_count(rows: list) -> int:
    return sum(1 for r in rows if r["flagged"])


def reach_series(canal: dict) -> dict:
    """Head-to-tail reach values for a canal's expander chart, or a reason it is
    unavailable. Never fabricates points."""
    eq = canal.get("head_tail_equity", {})
    if eq.get("status") != "OK":
        return {"available": False,
                "reason": eq.get("reason", eq.get("status", "not available"))}
    reaches = eq.get("reaches", [])
    if not reaches:
        return {"available": False, "reason": "no reach values"}
    return {
        "available": True,
        "positions": [r["position_along_canal"] for r in reaches],
        "ndvi": [r["mean_ndvi"] for r in reaches],
        "head_fit": eq.get("head_fit_ndvi"),
        "tail_fit": eq.get("tail_fit_ndvi"),
        "gap": eq.get("head_tail_gap"),
        "ci": eq.get("head_tail_gap_ci95"),
        "r2": eq.get("fit_r2"),
        "flagged": bool(eq.get("flagged")),
        "caveat": eq.get("attribution_caveat", ""),
    }


def nutrition_summary(canal: dict) -> dict:
    """Nutrition at whatever claim level the evidence supports, phrased for a
    manager. Never promotes a relative reading to an absolute nitrogen number."""
    n = canal.get("nutrition")
    if not n or n.get("status") != "OK":
        return {"available": False,
                "reason": (n or {}).get("reason", "not available")}
    level = n.get("claim_level", "relative")
    if level == "calibrated" and n.get("nitrogen_pct") is not None:
        conf = n.get("nitrogen_confidence", {})
        headline = (f"Leaf N {n['nitrogen_pct']}% "
                    f"(RMSE {conf.get('rmse_pct')}%, R² {conf.get('r2')}, "
                    f"n={conf.get('n_points')})")
    elif level == "sufficiency" and n.get("sufficiency_index") is not None:
        headline = f"Sufficiency vs strip: {n['sufficiency_index']} — {n.get('sufficiency_reading','')}"
    else:
        headline = f"Relative condition: {n.get('relative_condition','—')}"
    return {"available": True, "claim_level": level, "headline": headline,
            "caveat": n.get("caveat", "")}


def field_rows(results: dict) -> list:
    """
    One row per field for the field table.

    The column that matters is `verdict`. A field whose reference area was
    inadequate gets "no verdict" and a reason - NOT the word "healthy". The
    values are still shown, because they were genuinely measured; only the
    comparison against a threshold is missing. Collapsing those two states into
    one green tick is the single easiest way to turn this layer into a liar.
    """
    rows = []
    for f in results.get("fields", []):
        ind = (f.get("condition") or {}).get("indicators", {})
        ctx = (f.get("condition") or {}).get("context", {})
        ref = f.get("reference_provenance", {})
        withheld = bool(ref.get("verdict_withheld"))

        vig = ind.get("vigour", {})
        moist = ind.get("canopy_moisture", {})
        therm = ind.get("thermal_stress", {})

        rows.append({
            "name": f.get("name", "?"),
            "vigour_display": (_fmt(vig.get("value"))
                               if vig.get("status") == "OK" else "not available"),
            "moisture_display": (_fmt(moist.get("value"))
                                 if moist.get("status") == "OK" else "not available"),
            "thermal_display": (f"{therm.get('value')} °C"
                                if therm.get("status") == "OK" else "not available"),
            "rainfall_14d": ctx.get("rainfall_mm_last_14d"),
            "verdict": "no verdict" if withheld else (ctx.get("reading") or "—"),
            "verdict_withheld": withheld,
            "verdict_reason": (ref.get("reference_source") if withheld else None),
            "reference_source": ref.get("reference_source", "—").split(":")[0],
            "reference_ratio": ref.get("area_ratio"),
            "nutrition": nutrition_summary(f),
        })
    return rows


def fields_without_verdict(rows: list) -> int:
    """How much of the field table is measurement without judgement. Worth a
    headline number: if it is most of the table, the command-area geometry is
    the thing to fix, not the imagery."""
    return sum(1 for r in rows if r["verdict_withheld"])


def continuity_summary(canal: dict) -> dict:
    """
    Where the water stopped, phrased as a place rather than a percentage.

    "Water was not detected beyond reach 4 of 8" sends someone to a location.
    "Canal 40% wet" sends nobody anywhere, and the two can describe the same
    canal. The unobserved count is carried through because a manager reading
    "3 dry" needs to know whether the other reaches were checked.
    """
    c = canal.get("continuity") or {}
    if c.get("status") != "OK":
        return {"available": False,
                "reason": c.get("reason", c.get("status", "not available"))}
    fd = c.get("first_dry_reach")
    n = c.get("n_reaches")
    # Order matters. A canal dry from reach 1 has first_dry_reach == 1, and
    # "water not detected beyond reach 0" is not a sentence about anywhere. The
    # no-water case is checked first so it gets its own phrasing.
    if not c.get("wet_reaches"):
        headline = "no standing water detected in any reach"
    elif fd:
        headline = f"water not detected beyond reach {fd - 1} of {n}"
    else:
        headline = f"no break detected across {n} reaches"
    res = c.get("resolvability") or {}
    return {
        "available": True,
        "headline": headline,
        "states": c.get("states", []),
        "wet": c.get("wet_reaches"),
        "dry": c.get("dry_reaches"),
        "unobserved": c.get("unobserved_reaches"),
        "longest_dry_run": c.get("longest_dry_run"),
        "resolvable": res.get("resolvable"),
        "resolvability_note": res.get("note", ""),
        "caveat": c.get("interpretation", ""),
    }


# Reach glyphs. Deliberately NOT emoji: emoji fall back to a literal "?" when a
# glyph is missing from the system font, which is exactly what a genuinely
# unobserved reach also looks like - two different meanings rendering as the
# same character. Block elements exist in every font, and Streamlit's colour
# markdown carries the meaning rather than the shape alone.
_REACH_GLYPH = {
    "WET": ":blue[█]",
    "DRY": ":red[█]",          # the break is the thing to notice
    "UNOBSERVED": ":gray[░]",
}
REACH_LEGEND = ":blue[█] water detected · :red[█] not detected · :gray[░] not observed"


def continuity_strip(states: list) -> str:
    """Head-to-tail reach strip as Streamlit colour markdown.

    Lives here rather than in the view so the mapping is unit-tested: a strip
    that renders a dry reach and an unobserved reach identically would erase
    the distinction the whole continuity layer is built on.
    """
    return " ".join(_REACH_GLYPH.get(s, ":gray[?]") for s in states or [])


def efficiency_summary(canal: dict) -> dict:
    """
    Consumption always; the ratio only when a real release volume exists.

    The dashboard must not show an empty efficiency cell that reads as zero or
    as "efficient". It shows the consumption that was measured and states, in
    words, that the denominator is missing and where it would have to come from.
    """
    w = canal.get("water_use_efficiency") or {}
    if w.get("status") != "OK":
        return {"available": False,
                "reason": w.get("reason", "not available")}
    out = {
        "available": True,
        "consumed_m3": w.get("consumed_m3"),
        "area_ha": w.get("command_area_ha"),
        "efficiency": w.get("efficiency"),
    }
    if w.get("efficiency") is None:
        out["headline"] = (f"{w.get('consumed_m3', 0):,.0f} m³ consumed; "
                           "efficiency not available")
        out["reason"] = w.get("efficiency_reason", "")
    else:
        out["headline"] = f"efficiency {w['efficiency']}"
        out["caveat"] = w.get("efficiency_caveat", "")
    return out


def water_requirement_summary(record: dict) -> dict:
    """
    Crop water requirement for a canal command or a field, phrased so the
    requirement/delivery distinction survives the trip to the screen.

    The headline deliberately contains the word "needed". A dashboard cell
    reading "310 mm" next to a canal name would be read as supply by anyone
    skimming, and skimming is how dashboards are read.
    """
    w = record.get("water_requirement") or {}
    if w.get("status") != "OK":
        return {"available": False,
                "reason": w.get("reason", "not available")}
    deficit = w.get("irrigation_requirement_mm")
    out = {
        "available": True,
        "et0_mm": w.get("et0_mm"),
        "etc_mm": w.get("etc_mm"),
        "kcb": w.get("kcb"),
        "deficit_mm": deficit,
        "headline": (f"crop needed {w.get('etc_mm')} mm"
                     if w.get("etc_mm") is not None else "ET0 only"),
        "caveat": w.get("etc_caveat", ""),
    }
    if deficit is not None:
        out["headline"] += f"; {deficit} mm beyond rainfall"
    return out


def rangeland_rows(results: dict) -> list:
    """One row per rangeland area. A REFUSED area is shown as refused with its
    reason, not dropped: a silently missing row would look like an area that was
    never submitted."""
    rows = []
    for r in results.get("rangeland", []):
        if r.get("status") == "REFUSED":
            rows.append({"name": r.get("name", "?"), "status": "REFUSED",
                         "reason": r.get("reason"), "productivity": "—",
                         "verdict": "—", "greenup_day": "—", "water": "—"})
            continue
        prod = r.get("productivity", {}) or {}
        timing = r.get("timing", {}) or {}
        water = r.get("water_points", {}) or {}
        rows.append({
            "name": r.get("name", "?"),
            "status": r.get("status", "—"),
            "reason": None,
            "productivity": (_fmt(prod.get("ndvi_integral"))
                             if prod.get("status") == "OK" else "not available"),
            "verdict": prod.get("verdict") or "—",
            "greenup_day": (timing.get("greenup_day")
                            if timing.get("status") == "OK" else "not available"),
            "water": (_pct(water.get("water_frequency"))
                      if water.get("status") == "OK" else "not available"),
        })
    return rows


def forecast_summary(results: dict) -> dict:
    """The 7-day outlook, always carrying its resolution caveat. A forecast
    displayed without it invites being read as a field-scale prediction."""
    f = results.get("forecast") or {}
    if f.get("status") != "OK":
        return {"available": False, "reason": f.get("reason", "not available")}
    return {
        "available": True,
        "horizon_days": f.get("horizon_days"),
        "temperature_c": f.get("mean_temperature_c"),
        "precipitation": f.get("mean_precipitation_mm_per_step"),
        "caveat": (f.get("provenance", {}) or {}).get("note", ""),
    }


def provenance_lines(indicator: dict) -> list:
    """Flatten an indicator's provenance into human lines for a details panel."""
    p = indicator.get("provenance") or {}
    lines = []
    if p.get("sensor"):
        lines.append(f"Sensor: {p['sensor']}")
    if p.get("date_start"):
        lines.append(f"Dates: {p.get('date_start')} → {p.get('date_end')}")
    if p.get("n_scenes") is not None:
        lines.append(f"Scenes: {p['n_scenes']}")
    if p.get("observed_fraction") is not None:
        lines.append(f"Area observed: {100 * p['observed_fraction']:.0f}%")
    if p.get("threshold_basis"):
        lines.append(f"Threshold: {p['threshold_basis']}")
    if p.get("notes"):
        lines.append(p["notes"])
    return lines
