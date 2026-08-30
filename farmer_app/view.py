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
    DEPRECATED. Kept only so an external caller does not break.

    variables_table now generates its cells in the requested language directly.
    This function translated them afterwards by matching the generated English,
    which fails the moment the engine rewords anything - and fails by leaving
    English inside an Arabic table rather than by raising, which is the worst
    way for a translation to break.
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


# Engine vocabularies that reach the screen as cell values. Translated here
# rather than by matching the generated sentences afterwards: string matching on
# generated text is a trap that fails silently the moment the engine rewords
# anything, and it fails by showing English inside an Arabic table rather than
# by raising.
THERMAL_READING = {
    "warmer than the surrounding land": ("أدفأ من الأرض المحيطة",
                                         "warmer than the surrounding land"),
    "cooler than the surrounding land": ("أبرد من الأرض المحيطة",
                                         "cooler than the surrounding land"),
    "close to the surrounding land": ("قريب من الأرض المحيطة",
                                      "close to the surrounding land"),
}

SEASON_VERDICT = {
    "MUCH DRIER than this site's recent seasons":
        ("أجفّ بكثير من مواسم هذا الموقع الأخيرة",
         "MUCH DRIER than this site's recent seasons"),
    "drier than usual": ("أجفّ من المعتاد", "drier than usual"),
    "near this site's normal": ("قريب من معدّل هذا الموقع",
                                "near this site's normal"),
    "wetter than usual": ("أمطر من المعتاد", "wetter than usual"),
    "MUCH WETTER than usual": ("أمطر بكثير من المعتاد",
                               "MUCH WETTER than usual"),
}

RELATIVE_CONDITION = {
    "BELOW SCHEME NORM": ("دون معدّل المخطط", "BELOW SCHEME NORM"),
    "WITHIN SCHEME NORM": ("ضمن معدّل المخطط", "WITHIN SCHEME NORM"),
    "ABOVE SCHEME NORM": ("فوق معدّل المخطط", "ABOVE SCHEME NORM"),
}

SUFFICIENCY_READING = {
    "deficient": ("ناقص", "deficient"),
    "marginal": ("حدّي", "marginal"),
    "sufficient": ("كافٍ", "sufficient"),
}

SOIL_TEXTURE = {
    "clay": ("طين", "clay"),
    "silty clay": ("طين طَمْيي", "silty clay"),
    "sandy clay": ("طين رملي", "sandy clay"),
    "clay loam": ("طَفال طيني", "clay loam"),
    "silty clay loam": ("طَفال طيني طَمْيي", "silty clay loam"),
    "sandy clay loam": ("طَفال طيني رملي", "sandy clay loam"),
    "loam": ("طَفال", "loam"),
    "silt loam": ("طَفال طَمْيي", "silt loam"),
    "sandy loam": ("طَفال رملي", "sandy loam"),
    "silt": ("طَمْي", "silt"),
    "loamy sand": ("رمل طَفالي", "loamy sand"),
    "sand": ("رمل", "sand"),
    "unknown": ("غير معروف", "unknown"),
}


def _tr(table: dict, key, ar: bool, default="—") -> str:
    """Translate a known engine value, passing anything unrecognised through
    verbatim so a new vocabulary item is visible rather than silently blanked."""
    if key is None:
        return default
    pair = table.get(key)
    if not pair:
        return str(key)
    return pair[0] if ar else pair[1]


def variables_table(record: dict, ar: bool = False) -> list:
    """
    Every measured variable for one field, as display rows, in one language.

    Each row carries its sensor and the scale it was measured at, because a
    100 m thermal reading and a 10 m vigour reading are not the same kind of
    statement about a small field and the farmer should be able to see which is
    which without reading documentation. Sensor names and units stay in Latin
    script in both languages: they are identifiers to be checked against a
    catalogue, not prose to be read.
    """
    NA = "غير متاح" if ar else "not available"
    DASH = "—"
    rows = []
    readings = (record.get("crop_health") or {}).get("readings", {})

    for key, ar_label, en_label in (
            ("vigour", "النموّ (NDVI)", "Vigour (NDVI)"),
            ("canopy_moisture", "رطوبة الغطاء (NDMI)", "Canopy moisture (NDMI)"),
            ("greenness", "الاخضرار (EVI)", "Greenness (EVI)")):
        label_txt = ar_label if ar else en_label
        r = readings.get(key, {})
        if r.get("status") == "OK":
            thr = r.get("threshold")
            below = (thr is not None and r.get("value") is not None
                     and r["value"] < thr)
            rows.append({
                "variable": label_txt,
                "value": _num(r.get("value"), 4),
                "threshold": (_num(thr, 4) if thr is not None
                              else ("بلا عتبة" if ar else "no threshold")),
                "verdict": (("دون العتبة" if ar else "BELOW threshold") if below
                            else ("فوق العتبة" if ar else "above threshold")
                            if thr is not None else DASH),
                "below": below,
                "sensor": r.get("sensor", ""),
                "scale": f"{r.get('scale_m')} m" if r.get("scale_m") else "",
            })
        else:
            rows.append({"variable": label_txt, "value": NA,
                         "threshold": DASH, "verdict": DASH,
                         "sensor": r.get("sensor", ""),
                         "scale": "", "reason": r.get("reason", "")})

    th = record.get("thermal_stress") or {}
    if th.get("status") == "OK":
        rows.append({
            "variable": "حرارة السطح" if ar else "Surface temperature",
            "value": f"{th.get('value')} °C",
            "threshold": ((f"{th.get('neighbourhood_c')} °C حوله" if ar
                           else f"{th.get('neighbourhood_c')} °C around it")
                          if th.get("neighbourhood_c") is not None else DASH),
            "verdict": _tr(THERMAL_READING, th.get("reading"), ar),
            "sensor": th.get("sensor", ""),
            "scale": f"{th.get('scale_m')} m",
        })
        # The engine works out how many 100 m pixels the field spans. Below
        # about two, field and neighbourhood share most of their pixels and the
        # difference between them is suppressed by resolution rather than
        # measured - so the row carries that rather than letting a suspiciously
        # round difference read as a finding.
        if th.get("resolvable") is False and th.get("pixels_across"):
            rows[-1]["reason"] = (
                f"نحو {th['pixels_across']} بكسل حراري عرضًا — الحقل ومحيطه "
                "يتقاسمان البكسلات نفسها إلى حدّ بعيد، فالفرق بينهما مقموع "
                "بالتمييز لا مقيس." if ar else th.get("resolvability_note", ""))
    else:
        rows.append({"variable": "حرارة السطح" if ar else "Surface temperature",
                     "value": NA, "threshold": DASH, "verdict": DASH,
                     "sensor": "Landsat 8/9", "scale": "100 m",
                     "reason": th.get("reason", "")})

    rain = record.get("rainfall") or {}
    for key, ar_label, en_label in (
            ("season_mm", "مطر الموسم", "Rainfall, season"),
            ("last_14d_mm", "مطر آخر 14 يومًا", "Rainfall, last 14 days")):
        v = rain.get(key)
        rows.append({
            "variable": ar_label if ar else en_label,
            "value": f"{v} mm" if v is not None else NA,
            "threshold": DASH, "verdict": DASH,
            "sensor": rain.get("sensor", "CHIRPS"), "scale": "5.5 km"})

    wr = record.get("water_requirement") or {}
    if wr.get("status") == "OK":
        rows.append({
            "variable": "البخر-نتح المرجعي ET0" if ar else "Reference ET0",
            "value": f"{wr.get('et0_mm')} mm", "threshold": DASH,
            "verdict": f"{wr.get('et0_mm_per_day')} mm/"
                       + ("يوم" if ar else "day"),
            "sensor": "ERA5-Land, FAO-56", "scale": "11 km"})
        if wr.get("etc_mm") is not None:
            approx = str(wr.get("etc_method", "")).startswith("APPROXIMATE")
            verdict = "احتياج، لا ما وصل" if ar else "NEEDED, not received"
            if approx:
                verdict += " — طريقة تقريبية" if ar else " — APPROXIMATE method"
            rows.append({
                "variable": ("الماء الذي احتاجه المحصول (ETc)" if ar
                             else "Crop water NEEDED (ETc)"),
                "value": f"{wr.get('etc_mm')} mm",
                "threshold": f"Kcb {wr.get('kcb')}",
                "verdict": verdict,
                "sensor": "ERA5-Land + Sentinel-2", "scale": "11 km"})
    else:
        rows.append({"variable": ("الماء الذي احتاجه المحصول (ETc)" if ar
                                  else "Crop water NEEDED (ETc)"),
                     "value": NA, "threshold": DASH, "verdict": DASH,
                     "sensor": "ERA5-Land", "scale": "11 km",
                     "reason": wr.get("reason", "")})

    ph = record.get("phenology") or {}
    if ph.get("status") == "OK":
        gd = ph.get("greenup_day")
        rows.append({
            "variable": "يوم الإنبات" if ar else "Green-up day",
            "value": (DASH if gd is None else
                      (f"اليوم {int(gd)} من الموسم" if ar
                       else f"day {int(gd)} of the season")),
            "threshold": DASH,
            "verdict": (f"الذروة يوم {int(ph['peak_day'])}" if ar
                        else f"peak on day {int(ph['peak_day'])}"),
            "sensor": "Sentinel-2 series", "scale": "10 m"})
        sl = ph.get("season_length_days")
        rows.append({
            "variable": "طول الموسم" if ar else "Season length",
            "value": (DASH if sl is None else
                      (f"{int(sl)} يومًا" if ar else f"{int(sl)} days")),
            "threshold": DASH,
            "verdict": (f"ذروة NDVI {ph.get('peak_ndvi')}" if ar
                        else f"peak NDVI {ph.get('peak_ndvi')}"),
            "sensor": "Sentinel-2 series", "scale": "10 m"})
    else:
        rows.append({"variable": ("الإنبات / طول الموسم" if ar
                                  else "Green-up / season length"),
                     "value": NA, "threshold": DASH, "verdict": DASH,
                     "sensor": "Sentinel-2 series", "scale": "10 m",
                     "reason": ph.get("reason", "no phenology computed")})

    clim = record.get("climate") or {}
    gdd = clim.get("growing_degree_days")
    rows.append({
        "variable": "درجات النموّ الحرارية" if ar else "Growing degree days",
        "value": NA if gdd is None else f"{round(gdd)}",
        "threshold": (f"الأساس {clim.get('gdd_base_c')} °C" if ar
                      else f"base {clim.get('gdd_base_c')} °C"),
        "verdict": DASH, "sensor": "ERA5-Land", "scale": "11 km"})
    hsd = clim.get("heat_stress_days")
    rows.append({
        "variable": "أيام الإجهاد الحراري" if ar else "Heat-stress days",
        "value": (NA if hsd is None else
                  (f"{round(hsd)} يومًا" if ar else f"{round(hsd)} days")),
        "threshold": (f"فوق {clim.get('heat_stress_threshold_c')} °C" if ar
                      else f"above {clim.get('heat_stress_threshold_c')} °C"),
        "verdict": DASH, "sensor": "ERA5-Land", "scale": "11 km"})

    ds = clim.get("dry_spells") or {}
    dsl = ds.get("longest_dry_spell_days")
    rows.append({
        "variable": "أطول فترة جفاف" if ar else "Longest dry spell",
        "value": (NA if dsl is None else
                  (f"{dsl} يومًا" if ar else f"{dsl} days")),
        "threshold": ((f"يُعلَّم عند {ds.get('threshold_days')} يومًا" if ar
                       else f"flag at {ds.get('threshold_days')} days")
                      if ds.get("threshold_days") else DASH),
        "verdict": (("معلَّمة" if ar else "FLAGGED") if ds.get("flagged")
                    else DASH),
        "sensor": "CHIRPS", "scale": "5.5 km"})

    svh = clim.get("season_vs_history") or {}
    tsm = svh.get("this_season_mm")
    rows.append({
        "variable": ("الموسم مقابل تاريخ الموقع" if ar
                     else "Season vs this site's history"),
        "value": f"{tsm} mm" if tsm is not None else NA,
        "threshold": DASH,
        "verdict": _tr(SEASON_VERDICT, svh.get("verdict"), ar),
        "sensor": "CHIRPS, 10 " + ("سنوات" if ar else "years"),
        "scale": "5.5 km"})

    soil = record.get("soil") or {}
    rows.append({
        "variable": "قوام التربة" if ar else "Soil texture",
        "value": (_tr(SOIL_TEXTURE, soil.get("texture"), ar, NA)
                  if soil.get("texture") else NA),
        "threshold": DASH, "verdict": DASH,
        "sensor": "OpenLandMap model", "scale": "250 m"})

    return rows


def etc_method_note(record: dict, ar: bool = False) -> Optional[str]:
    """Say which ETc method produced the number, because the two differ by
    around a fifth on a real Gezira field and the difference is systematic,
    not noise."""
    wr = record.get("water_requirement") or {}
    method = wr.get("etc_method")
    if not method:
        return None
    cs = wr.get("canopy_series") or {}
    if str(method).startswith("APPROXIMATE"):
        if ar:
            return ("⚠️ طريقة تقريبية: متوسّط NDVI الموسمي حُوّل إلى معامل "
                    "واحد وضُرب في ET0 الكلي. هذا يساوي التكامل الحقيقي فقط إذا "
                    "لم يرتبط الغطاء بـ ET0 عبر الموسم، وفي موسم ريّ هنا هما "
                    "مرتبطان — فالأسابيع العارية هي الأشدّ حرارة.")
        return "⚠️ " + method
    cov = cs.get("coverage")
    if cov is None:
        return method
    if ar:
        return (f"ETc هو التكامل اليومي لـ Kcb(t) × ET0(t) عبر "
                f"{cs.get('observed_days', '?')} مشهدًا صافيًا، تغطّي "
                f"{round(100 * cov)}% من الموسم.")
    return (f"ETc is the daily integral of Kcb(t) × ET0(t) over "
            f"{cs.get('observed_days', '?')} cloud-free scenes, covering "
            f"{round(100 * cov)}% of the season.")


def rows_to_csv(rows: list, ar: bool = False) -> str:
    """
    The measurement rows as CSV.

    st.dataframe gave sorting, column resizing and a download button for free.
    The styled table replaced it because a dataframe cannot colour a
    below-threshold reading or set an unavailable row in italic grey - and in a
    dataframe "not available" and a merely low number look identical, which is
    the one distinction this whole engine turns on. This function gives the
    export back rather than leaving the trade half-made.

    The header row is translated with everything else, but the sensor and scale
    values are not: a CSV is usually opened to be checked, and a translated
    sensor name is harder to check against the catalogue.
    """
    import csv
    import io as _io

    head = [label({"variable": ("المتغيّر", "Variable")}, "variable", ar),
            "القيمة" if ar else "Value",
            "مقارنًا بـ" if ar else "Compared with",
            "القراءة" if ar else "Reading",
            "المستشعر" if ar else "Sensor",
            "قيس عند" if ar else "Measured at",
            "السبب" if ar else "Reason"]
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    for r in rows:
        w.writerow([r.get("variable", ""), r.get("value", ""),
                    r.get("threshold", ""), r.get("verdict", ""),
                    r.get("sensor", ""), r.get("scale", ""),
                    r.get("reason", "")])
    return buf.getvalue()


def nutrition_line(record: dict, ar: bool = False) -> dict:
    """
    Nutrition at the strongest claim level the evidence supports, never higher,
    with the ladder step named so the farmer can see what would be needed to
    move up it.

    The caveat is the sentence that most needs to be understood - chlorophyll
    falls for half a dozen reasons besides nitrogen - so it is the last sentence
    that should be left in the wrong language.
    """
    n = record.get("nutrition") or {}
    if n.get("status") != "OK":
        return {"available": False,
                "reason": n.get("reason",
                                "غير متاح" if ar else "not available")}

    caveat = (
        "مؤشّرات الكلوروفيل تستجيب للنيتروجين، وتستجيب أيضًا لإجهاد الماء "
        "والملوحة والمرض ونقص الكبريت والحديد والحرارة. القيمة المنخفضة تدلّ "
        "على محصول متعثّر، ولا تحدّد السبب بذاتها."
        if ar else n.get("caveat", ""))

    level = n.get("claim_level", "relative")
    if level == "calibrated" and n.get("nitrogen_pct") is not None:
        conf = n.get("nitrogen_confidence") or {}
        headline = (f"نيتروجين الورقة {n['nitrogen_pct']}% "
                    f"(خطأ {conf.get('rmse_pct')}%، n={conf.get('n_points')})"
                    if ar else
                    f"Leaf nitrogen {n['nitrogen_pct']}% "
                    f"(RMSE {conf.get('rmse_pct')}%, n={conf.get('n_points')})")
        next_step = None
    elif level == "sufficiency" and n.get("sufficiency_index") is not None:
        reading = _tr(SUFFICIENCY_READING, n.get("sufficiency_reading"), ar, "")
        headline = (f"الكفاية مقابل شريطك المرجعي: {n['sufficiency_index']}"
                    + (f" — {reading}" if reading and reading != "—" else "")
                    if ar else
                    f"Sufficiency vs your reference strip: "
                    f"{n['sufficiency_index']} — "
                    f"{n.get('sufficiency_reading','')}")
        next_step = ("لنسبة نيتروجين مطلقة، يلزم 30 قياس ورقة أو SPAD محليًّا "
                     "على الأقل لهذا المحصول." if ar else
                     "For an absolute nitrogen percentage, 30 or more local "
                     "leaf or SPAD measurements are needed for this crop.")
    else:
        cond = _tr(RELATIVE_CONDITION, n.get("relative_condition"), ar)
        headline = (f"حالة الكلوروفيل: {cond}" if ar
                    else f"Chlorophyll condition: {cond}")
        next_step = ("اترك في الحقل شريطًا مرجعيًّا مفرط التسميد للانتقال من "
                     "ترتيب نسبي إلى مؤشّر كفاية." if ar else
                     "Leave an over-fertilised reference strip in the field to "
                     "move from a ranking to a sufficiency index.")
    return {"available": True, "level": level, "headline": headline,
            "next_step": next_step, "caveat": caveat}


def yield_line(record: dict, ar: bool = False) -> str:
    """The tonnage, or the reason there is none. The refusal is the common case
    and is the more important of the two to render in the reader's language."""
    y = record.get("yield_estimate") or {}
    if y.get("yield_t_ha") is not None:
        c = y.get("confidence") or {}
        return (f"{y['yield_t_ha']} طن/هكتار "
                f"(خطأ {c.get('rmse_fraction')}، n={c.get('n_points')})" if ar
                else f"{y['yield_t_ha']} t/ha "
                     f"(RMSE {c.get('rmse_fraction')}, n={c.get('n_points')})")
    if ar:
        return ("لا يوجد نموذج إنتاجية معايَر لهذا المحصول؛ يلزم 30 قياس حصاد "
                "محلي على الأقل قبل ذكر أي كمية.")
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
    """
    Translate an engine-generated driver sentence.

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
