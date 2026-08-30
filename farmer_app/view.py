"""
Display logic for the farmer app - kept out of the Streamlit file so it can be
tested without a browser.

Named `view` rather than `data` on purpose. Both this app and the scheme
dashboard put their display logic beside their Streamlit file, and a second
module called `data` on sys.path shadowed the first: the farmer tests passed
alone and all 24 failed in the full suite, because whichever module imported
first won in sys.modules. Two apps in one repo cannot both own a name that
generic.

THE ONE RULE THAT GOVERNS EVERY FUNCTION HERE
---------------------------------------------
A map is more persuasive than a table, and that cuts both ways. A field drawn in
confident green because nothing could be measured on it is a worse lie than a
blank cell, because nobody reads a colour sceptically. So the palette has a
THIRD state - grey for unmeasured - and it is never collapsed into either green
or red. "We could not see this field" and "this field is fine" must not look
alike from across the room.
"""

from __future__ import annotations

from typing import Optional


# Colours as RGBA, chosen to stay distinguishable for the commonest forms of
# colour blindness: the red/green pair is separated by lightness as well as hue,
# and grey is unmistakably neither.
COLOUR_ATTENTION = [200, 60, 45, 190]      # below the neighbourhood threshold
COLOUR_WATCH = [235, 165, 55, 180]         # measured, low within the farm
COLOUR_OK = [70, 150, 95, 170]             # measured, not low
COLOUR_UNMEASURED = [130, 130, 135, 130]   # NOT measured - never green, never red

# Bilingual throughout. An interface whose chrome is Arabic and whose content is
# English asks a farmer to switch language mid-sentence to use their own tool -
# and the sentence they would have to switch for is usually the caveat, which is
# the part that most needs to be understood.
LEGEND_BI = [
    ("attention", ("تحتاج انتباهًا", "needs attention"),
     ("النموّ دون العتبة المشتقّة من جوار هذا الحقل نفسه",
      "vigour below the threshold derived from this field's own neighbourhood")),
    ("watch", ("للمراقبة", "watch"),
     ("مقيسة، ومنخفضة مقارنةً ببقية حقول هذه المزرعة",
      "measured, and low compared with the other fields on this farm")),
    ("ok", ("سليمة", "ok"),
     ("مقيسة، وغير منخفضة", "measured, and not low")),
    ("unmeasured", ("لم تُقَس", "not measured"),
     ("لا قراءة أقمار صالحة — وهذا ليس حقلًا سليمًا، بل حقلًا لم يُرَ",
      "no usable satellite reading — this is NOT a healthy field, it is an "
      "unseen one")),
]

# Kept for English-only callers and for the colour-consistency test.
LEGEND = [(en_label, colour, en_meaning)
          for (_key, (_ar_label, en_label), (_ar_m, en_meaning)), colour
          in zip(LEGEND_BI, [COLOUR_ATTENTION, COLOUR_WATCH, COLOUR_OK,
                             COLOUR_UNMEASURED])]

STATUS_LABEL = {k: lbl for k, lbl, _m in LEGEND_BI}

VARIABLE_LABEL = {
    "Vigour (NDVI)": ("النموّ (NDVI)", "Vigour (NDVI)"),
    "Canopy moisture (NDMI)": ("رطوبة الغطاء (NDMI)", "Canopy moisture (NDMI)"),
    "Greenness (EVI)": ("الاخضرار (EVI)", "Greenness (EVI)"),
    "Surface temperature": ("حرارة السطح", "Surface temperature"),
    "Rainfall, season": ("مطر الموسم", "Rainfall, season"),
    "Rainfall, last 14 days": ("مطر آخر 14 يومًا", "Rainfall, last 14 days"),
    "Reference ET0": ("البخر-نتح المرجعي ET0", "Reference ET0"),
    "Crop water NEEDED (ETc)": ("الماء الذي احتاجه المحصول (ETc)",
                                "Crop water NEEDED (ETc)"),
    "Green-up day": ("يوم الإنبات", "Green-up day"),
    "Season length": ("طول الموسم", "Season length"),
    "Green-up / season length": ("الإنبات / طول الموسم",
                                 "Green-up / season length"),
    "Growing degree days": ("درجات النموّ الحرارية", "Growing degree days"),
    "Heat-stress days": ("أيام الإجهاد الحراري", "Heat-stress days"),
    "Longest dry spell": ("أطول فترة جفاف", "Longest dry spell"),
    "Season vs this site's history": ("الموسم مقابل تاريخ الموقع",
                                      "Season vs this site's history"),
    "Soil texture": ("قوام التربة", "Soil texture"),
}

VERDICT_LABEL = {
    "BELOW threshold": ("دون العتبة", "BELOW threshold"),
    "above threshold": ("فوق العتبة", "above threshold"),
    "not available": ("غير متاح", "not available"),
    "no threshold": ("بلا عتبة", "no threshold"),
}


def label(table: dict, key: str, ar: bool) -> str:
    """Look up a bilingual label, falling back to the key itself so an
    untranslated string appears verbatim rather than vanishing."""
    pair = table.get(key)
    if not pair:
        return key
    return pair[0] if ar else pair[1]


def localise_rows(rows: list, ar: bool) -> list:
    """
    Translate variable names, verdicts and the two stock cell values.

    Values, sensors and scales are left alone: a sensor name and a unit are not
    language, and translating "Sentinel-2" would make the provenance harder to
    check rather than easier to read.
    """
    if not ar:
        return rows
    out = []
    for r in rows:
        c = dict(r)
        c["variable"] = label(VARIABLE_LABEL, r["variable"], True)
        for src, (ar_txt, _en) in VERDICT_LABEL.items():
            if str(r.get("verdict", "")) == src:
                c["verdict"] = ar_txt
            if str(r.get("value", "")) == src:
                c["value"] = ar_txt
            if str(r.get("threshold", "")) == src:
                c["threshold"] = ar_txt
        if str(r.get("verdict", "")).startswith("NEEDED, not received"):
            c["verdict"] = str(r["verdict"]).replace(
                "NEEDED, not received", "احتياج، لا ما وصل")
        out.append(c)
    return out


def _num(v, nd=3, dash="—"):
    return dash if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def field_status(record: dict, farm_vigours: Optional[list] = None,
                 ar: bool = False) -> dict:
    """
    Classify one field for the map.

    Returns {"status", "colour", "vigour", "why"}. `status` is one of
    attention | watch | ok | unmeasured.

    The `watch` band exists because a farm often has no field below its
    threshold and still has a worst field, and that is the one a farmer wants
    to walk to. It is explicitly a WITHIN-FARM comparison and says so, so it is
    never mistaken for an absolute judgement.
    """
    readings = (record.get("crop_health") or {}).get("readings", {})
    vig = readings.get("vigour", {})
    if vig.get("status") != "OK" or vig.get("value") is None:
        return {"status": "unmeasured", "colour": COLOUR_UNMEASURED,
                "vigour": None,
                "why": ("لا قراءة أقمار صالحة لهذا الحقل" if ar
                        else vig.get("reason", "no usable satellite reading"))}

    v = vig["value"]
    thr = vig.get("threshold")
    if thr is not None and v < thr:
        return {"status": "attention", "colour": COLOUR_ATTENTION, "vigour": v,
                "why": (f"النموّ {v:.3f} دون العتبة {thr:.3f} المشتقّة من "
                        "جوار هذا الحقل" if ar else
                        f"vigour {v:.3f} is below the {thr:.3f} threshold "
                        "derived from this field's neighbourhood")}

    if farm_vigours and len(farm_vigours) >= 3:
        ordered = sorted(farm_vigours)
        cut = ordered[max(0, len(ordered) // 3 - 1)]
        if v <= cut:
            return {"status": "watch", "colour": COLOUR_WATCH, "vigour": v,
                    "why": (f"النموّ {v:.3f} ضمن أدنى ثلث حقول هذه المزرعة — "
                            "مقارنة داخل المزرعة، لا حكمًا مطلقًا" if ar else
                            f"vigour {v:.3f} is in the lowest third of THIS "
                            "farm's fields — a comparison within the farm, not "
                            "an absolute judgement")}

    return {"status": "ok", "colour": COLOUR_OK, "vigour": v,
            "why": (f"النموّ {v:.3f}، وليس دون عتبة الجوار" if ar else
                    f"vigour {v:.3f}, not below the neighbourhood threshold")}


def map_features(report: dict, field_fc: dict, ar: bool = False) -> list:
    """
    Join the engine's per-field results to the polygons for drawing.

    A polygon with no matching result is drawn UNMEASURED rather than dropped.
    A field silently missing from the map reads as a field the farmer does not
    have.
    """
    by_name = {f.get("name"): f for f in report.get("fields", [])}
    vigours = []
    for rec in report.get("fields", []):
        vig = ((rec.get("crop_health") or {}).get("readings", {})
               .get("vigour", {}))
        if vig.get("status") == "OK" and vig.get("value") is not None:
            vigours.append(vig["value"])

    out = []
    for feat in field_fc.get("features", []):
        name = (feat.get("properties") or {}).get("name", "")
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Polygon" or not geom.get("coordinates"):
            continue
        rec = by_name.get(name)
        if rec is None:
            st = {"status": "unmeasured", "colour": COLOUR_UNMEASURED,
                  "vigour": None,
                  "why": ("هذا الحقل ليس في التقرير — لم يُحلّل" if ar
                          else "this field is not in the report - it was not "
                               "analysed")}
        else:
            st = field_status(rec, vigours, ar)

        out.append({
            "name": name or "(unnamed)",
            "polygon": [[float(p[0]), float(p[1])] for p in geom["coordinates"][0]],
            "colour": st["colour"],
            "status": label(STATUS_LABEL, st["status"], ar),
            "status_key": st["status"],
            "vigour_display": _num(st["vigour"]),
            "why": st["why"],
        })
    return out


def variables_table(record: dict) -> list:
    """
    Every measured variable for one field, as display rows.

    Each row carries its sensor and the scale it was measured at, because a
    100 m thermal reading and a 10 m vigour reading are not the same kind of
    statement about a small field and the farmer should be able to see which is
    which without reading documentation.
    """
    rows = []
    readings = (record.get("crop_health") or {}).get("readings", {})

    for key, label in (("vigour", "Vigour (NDVI)"),
                       ("canopy_moisture", "Canopy moisture (NDMI)"),
                       ("greenness", "Greenness (EVI)")):
        r = readings.get(key, {})
        if r.get("status") == "OK":
            thr = r.get("threshold")
            rows.append({
                "variable": label,
                "value": _num(r.get("value"), 4),
                "threshold": _num(thr, 4) if thr is not None else "no threshold",
                "verdict": ("BELOW threshold" if thr is not None
                            and r.get("value") is not None
                            and r["value"] < thr else
                            "above threshold" if thr is not None else "—"),
                "sensor": r.get("sensor", ""),
                "scale": f"{r.get('scale_m')} m" if r.get("scale_m") else "",
            })
        else:
            rows.append({"variable": label, "value": "not available",
                         "threshold": "—", "verdict": "—",
                         "sensor": r.get("sensor", ""),
                         "scale": "", "reason": r.get("reason", "")})

    th = record.get("thermal_stress") or {}
    if th.get("status") == "OK":
        rows.append({
            "variable": "Surface temperature",
            "value": f"{th.get('value')} °C",
            "threshold": (f"{th.get('neighbourhood_c')} °C around it"
                          if th.get("neighbourhood_c") is not None else "—"),
            "verdict": th.get("reading", "—"),
            "sensor": th.get("sensor", ""),
            "scale": f"{th.get('scale_m')} m",
        })
    else:
        rows.append({"variable": "Surface temperature", "value": "not available",
                     "threshold": "—", "verdict": "—",
                     "sensor": "Landsat 8/9", "scale": "100 m",
                     "reason": th.get("reason", "")})

    rain = record.get("rainfall") or {}
    rows.append({
        "variable": "Rainfall, season",
        "value": (f"{rain.get('season_mm')} mm"
                  if rain.get("season_mm") is not None else "not available"),
        "threshold": "—", "verdict": "—",
        "sensor": rain.get("sensor", "CHIRPS"), "scale": "5.5 km"})
    rows.append({
        "variable": "Rainfall, last 14 days",
        "value": (f"{rain.get('last_14d_mm')} mm"
                  if rain.get("last_14d_mm") is not None else "not available"),
        "threshold": "—", "verdict": "—",
        "sensor": rain.get("sensor", "CHIRPS"), "scale": "5.5 km"})

    wr = record.get("water_requirement") or {}
    if wr.get("status") == "OK":
        rows.append({"variable": "Reference ET0",
                     "value": f"{wr.get('et0_mm')} mm",
                     "threshold": "—",
                     "verdict": f"{wr.get('et0_mm_per_day')} mm/day",
                     "sensor": "ERA5-Land, FAO-56", "scale": "11 km"})
        if wr.get("etc_mm") is not None:
            approx = str(wr.get("etc_method", "")).startswith("APPROXIMATE")
            rows.append({"variable": "Crop water NEEDED (ETc)",
                         "value": f"{wr.get('etc_mm')} mm",
                         "threshold": f"Kcb {wr.get('kcb')}",
                         "verdict": ("NEEDED, not received"
                                     + (" — APPROXIMATE method" if approx else "")),
                         "sensor": "ERA5-Land + Sentinel-2", "scale": "11 km"})
    else:
        rows.append({"variable": "Crop water NEEDED (ETc)",
                     "value": "not available", "threshold": "—", "verdict": "—",
                     "sensor": "ERA5-Land", "scale": "11 km",
                     "reason": wr.get("reason", "")})

    ph = record.get("phenology") or {}
    if ph.get("status") == "OK":
        rows.append({"variable": "Green-up day",
                     "value": f"day {int(ph['greenup_day'])} of the season"
                              if ph.get("greenup_day") is not None else "—",
                     "threshold": "—",
                     "verdict": f"peak on day {int(ph['peak_day'])}",
                     "sensor": "Sentinel-2 series", "scale": "10 m"})
        rows.append({"variable": "Season length",
                     "value": ("—" if ph.get("season_length_days") is None
                               else f"{int(ph['season_length_days'])} days"),
                     "threshold": "—",
                     "verdict": f"peak NDVI {ph.get('peak_ndvi')}",
                     "sensor": "Sentinel-2 series", "scale": "10 m"})
    else:
        rows.append({"variable": "Green-up / season length",
                     "value": "not available", "threshold": "—", "verdict": "—",
                     "sensor": "Sentinel-2 series", "scale": "10 m",
                     "reason": ph.get("reason", "no phenology computed")})

    clim = record.get("climate") or {}
    for key, label, unit in (("growing_degree_days", "Growing degree days", ""),
                             ("heat_stress_days", "Heat-stress days", " days")):
        v = clim.get(key)
        rows.append({
            "variable": label,
            "value": "not available" if v is None else f"{round(v)}{unit}",
            "threshold": (f"base {clim.get('gdd_base_c')} °C"
                          if key == "growing_degree_days"
                          else f"above {clim.get('heat_stress_threshold_c')} °C"),
            "verdict": "—", "sensor": "ERA5-Land", "scale": "11 km"})

    ds = (clim.get("dry_spells") or {})
    rows.append({
        "variable": "Longest dry spell",
        "value": ("not available" if ds.get("longest_dry_spell_days") is None
                  else f"{ds['longest_dry_spell_days']} days"),
        "threshold": (f"flag at {ds.get('threshold_days')} days"
                      if ds.get("threshold_days") else "—"),
        "verdict": "FLAGGED" if ds.get("flagged") else "—",
        "sensor": "CHIRPS", "scale": "5.5 km"})

    svh = clim.get("season_vs_history") or {}
    rows.append({
        "variable": "Season vs this site's history",
        "value": (f"{svh.get('this_season_mm')} mm"
                  if svh.get("this_season_mm") is not None else "not available"),
        "threshold": "—", "verdict": svh.get("verdict", "—"),
        "sensor": "CHIRPS, 10 years", "scale": "5.5 km"})

    soil = record.get("soil") or {}
    rows.append({
        "variable": "Soil texture",
        "value": soil.get("texture", "not available"),
        "threshold": "—", "verdict": "—",
        "sensor": "OpenLandMap model", "scale": "250 m"})

    return rows


def etc_method_note(record: dict) -> Optional[str]:
    """Say which ETc method produced the number, because the two differ by
    around a fifth on a real Gezira field and the difference is systematic,
    not noise."""
    wr = record.get("water_requirement") or {}
    method = wr.get("etc_method")
    if not method:
        return None
    cs = wr.get("canopy_series") or {}
    if str(method).startswith("APPROXIMATE"):
        return "⚠️ " + method
    cov = cs.get("coverage")
    return (f"ETc is the daily integral of Kcb(t) × ET0(t) over "
            f"{cs.get('observed_days', '?')} cloud-free scenes, covering "
            f"{round(100 * cov)}% of the season."
            if cov is not None else method)


def nutrition_line(record: dict) -> dict:
    """Nutrition at the strongest claim level the evidence supports, never
    higher, with the ladder step named so the farmer can see what would be
    needed to move up it."""
    n = record.get("nutrition") or {}
    if n.get("status") != "OK":
        return {"available": False, "reason": n.get("reason", "not available")}
    level = n.get("claim_level", "relative")
    if level == "calibrated" and n.get("nitrogen_pct") is not None:
        conf = n.get("nitrogen_confidence") or {}
        headline = (f"Leaf nitrogen {n['nitrogen_pct']}% "
                    f"(RMSE {conf.get('rmse_pct')}%, n={conf.get('n_points')})")
        next_step = None
    elif level == "sufficiency" and n.get("sufficiency_index") is not None:
        headline = (f"Sufficiency vs your reference strip: "
                    f"{n['sufficiency_index']} — {n.get('sufficiency_reading','')}")
        next_step = ("For an absolute nitrogen percentage, 30 or more local leaf "
                     "or SPAD measurements are needed for this crop.")
    else:
        headline = f"Chlorophyll condition: {n.get('relative_condition','—')}"
        next_step = ("Leave an over-fertilised reference strip in the field to "
                     "move from a ranking to a sufficiency index.")
    return {"available": True, "level": level, "headline": headline,
            "next_step": next_step, "caveat": n.get("caveat", "")}


def yield_line(record: dict) -> str:
    y = record.get("yield_estimate") or {}
    if y.get("yield_t_ha") is not None:
        c = y.get("confidence") or {}
        return (f"{y['yield_t_ha']} t/ha "
                f"(RMSE {c.get('rmse_fraction')}, n={c.get('n_points')})")
    return y.get("reason", "not available")


STATUS_MARK = {"attention": "🔴", "watch": "🟠", "ok": "🟢",
               "unmeasured": "⚪"}


DRIVER_PATTERNS = [
    ("vigour below the neighbourhood threshold",
     "النموّ دون عتبة الجوار"),
    ("canopy moisture below the neighbourhood threshold",
     "رطوبة الغطاء دون عتبة الجوار"),
]


def localise_driver(text: str, ar: bool) -> str:
    """Translate an engine-generated driver sentence.

    Anything unrecognised passes through in English rather than being dropped:
    a driver that vanishes because nobody translated it takes the reason for a
    field's rank with it, which is worse than a sentence in the wrong language.
    """
    if not ar:
        return text
    for en, arabic in DRIVER_PATTERNS:
        if text == en:
            return arabic
    if text.endswith("degC warmer than its surroundings"):
        return "أدفأ بـ" + text.split(" degC")[0] + "°م من محيطه"
    if "mm of water needed beyond rainfall" in text:
        return text.split(" mm")[0] + " مم ماءً احتاجها المحصول فوق المطر"
    return text


def attention_list(report: dict, ar: bool = False) -> dict:
    """
    The farm-level answer: which field first, and why.

    Each entry carries the SAME status the map uses. An earlier version marked
    the list from `below_threshold` alone while the map used the four-state
    classification, so a live run showed Field 2 amber on the map, green in the
    list, and "need attention: 0" in the header - three different answers to one
    question on one screen. A map and a list that disagree are worse than either
    alone, because the reader cannot tell which to believe.
    """
    r = report.get("ranking") or {}
    by_name = {f.get("name"): f for f in report.get("fields", [])}
    vigours = [x["vigour"] for x in r.get("ranked", [])
               if x.get("vigour") is not None]

    ranked = []
    for entry in r.get("ranked", []):
        rec = by_name.get(entry.get("name"))
        st = (field_status(rec, vigours, ar) if rec else
              {"status": "unmeasured", "why": "no record"})
        e = dict(entry)
        e["status"] = st["status"]
        e["status_label"] = label(STATUS_LABEL, st["status"], ar)
        e["mark"] = STATUS_MARK.get(st["status"], "⚪")
        e["why"] = st["why"]
        e["drivers"] = [localise_driver(d, ar) for d in e.get("drivers", [])]
        ranked.append(e)

    return {"ranked": ranked,
            "unmeasured": r.get("unmeasured", []),
            "n_attention": sum(1 for e in ranked if e["status"] == "attention"),
            "n_watch": sum(1 for e in ranked if e["status"] == "watch"),
            "basis": r.get("basis", ""),
            "unmeasured_note": r.get("unmeasured_note", "")}
