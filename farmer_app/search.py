"""
Finding a field: search, filters, and selection by drawing on the map.

WHY A LIST OF FOUR FIELDS NEEDS A SEARCH
----------------------------------------
It does not. A scheme does. Gezira is roughly two million feddans divided among
tens of thousands of tenancies, and a tool that only works while the farm fits
on one screen is a demonstration rather than a product. Everything here is
written against the list the report actually contains, so it costs nothing at
four fields and still works at four thousand.

THE RULE THIS MODULE INHERITS
-----------------------------
The rest of the app refuses to let "not measured" look like "fine". A filter
breaks that rule in a quieter way: if you filter by crop and a field has no
crop recorded, dropping it silently tells the reader that field is not sorghum,
when the truth is that nobody said what it is. So every filter here separates
two outcomes that a normal search engine merges:

    matched   - the field has the attribute and it matches
    unknown   - the field has no value for the attribute that was filtered on

`unknown` is returned, counted and displayed. It is never folded into "no
results". The distinction matters most exactly when the list is long enough
that nobody will notice a field going missing.

WHAT THE POLYGON SELECTION ACTUALLY TESTS
-----------------------------------------
Drawing a shape and asking "which fields are in here" is a containment
question, and containment has more than one answer for a field that straddles
the edge. This module tests whether the field's CENTROID falls inside the drawn
shape, and the app says so on screen. Centroid-in-polygon is the cheap,
predictable rule: a half-covered field is either in or out, once, and redrawing
the shape slightly does not flip a dozen edge fields in and out at random.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import decision_logic as dl  # noqa: E402

import view as V  # noqa: E402


# ==============================================================================
# TEXT NORMALISATION
# ==============================================================================

# Arabic is written with letters a keyboard produces several ways. Someone
# searching for "الجزيره" should find "الجزيرة", and someone typing Arabic-Indic
# digits should find a field named with Western ones. Without this the search
# box appears broken to exactly the users it was built for.
_ARABIC_FOLD = {
    "أ": "ا", "إ": "ا", "آ": "ا",  # أ إ آ -> ا
    "ة": "ه",                                          # ة -> ه
    "ى": "ي",                                          # ى -> ي
    "ـ": "",                                                # tatweel
}
_ARABIC_DIGITS = {ord("٠") + i: str(i) for i in range(10)}        # ٠-٩
_ARABIC_DIGITS.update({ord("۰") + i: str(i) for i in range(10)})  # ۰-۹
# Harakat: a typed query almost never carries them, stored text sometimes does.
_DIACRITICS = dict.fromkeys(range(0x064B, 0x0653), None)


def normalise(text) -> str:
    """Fold a string to the form the search compares on."""
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = s.translate(_ARABIC_DIGITS).translate(_DIACRITICS)
    for src, dst in _ARABIC_FOLD.items():
        s = s.replace(src, dst)
    return " ".join(s.split())


# ==============================================================================
# THE INDEX
# ==============================================================================

def _iso(value) -> Optional[str]:
    """Accept a date, a datetime or a string; return YYYY-MM-DD or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value)[:10]
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return s


def _shift(iso: Optional[str], days) -> Optional[str]:
    if not iso or days is None:
        return None
    try:
        return (datetime.strptime(iso, "%Y-%m-%d")
                + timedelta(days=float(days))).date().isoformat()
    except (ValueError, TypeError):
        return None


def field_index(report: dict, field_fc: Optional[dict] = None,
                harvests: Optional[dict] = None, ar: bool = False) -> list:
    """
    One searchable row per field, assembled from the report, the polygons and
    whatever the farmer has recorded.

    Every derived date carries how it was arrived at. A green-up date read off
    an NDVI curve and a harvest date a farmer wrote down are both dates, and
    treating them as one kind of fact is how a satellite guess ends up in
    somebody's records as an observation.
    """
    harvests = harvests or {}
    season = report.get("season") or {}
    season_start = _iso(season.get("start"))
    farm_crop = report.get("crop")

    geom_by_name, props_by_name = {}, {}
    for feat in (field_fc or {}).get("features", []):
        props = feat.get("properties") or {}
        nm = props.get("name", "")
        geom_by_name[nm] = feat.get("geometry") or {}
        props_by_name[nm] = props

    vigours = []
    for rec in report.get("fields", []):
        vig = ((rec.get("crop_health") or {}).get("readings", {})
               .get("vigour", {}))
        if vig.get("status") == "OK" and vig.get("value") is not None:
            vigours.append(vig["value"])

    rows = []
    for rec in report.get("fields", []):
        name = rec.get("name", "")
        props = {**(rec.get("properties") or {}), **props_by_name.get(name, {})}
        geom = geom_by_name.get(name) or {}
        status = V.field_status(rec, vigours, ar)

        # Crop: whatever the field itself declares beats the farm default. A
        # report run for "sorghum" over a farm containing a wheat block would
        # otherwise label the wheat sorghum.
        crop = props.get("crop") or farm_crop
        crop_source = ("field" if props.get("crop")
                       else ("farm" if farm_crop else None))

        ph = rec.get("phenology") or {}
        ok = ph.get("status") == "OK"
        greenup = _shift(season_start, ph.get("greenup_day")) if ok else None
        length = ph.get("season_length_days") if ok else None

        sown = _iso(props.get("sowing_date") or props.get("sown"))
        harvest_recorded = _iso(harvests.get(name) or props.get("harvest_date"))
        harvest_expected = _shift(greenup, length)

        series = rec.get("series") or {}
        dates = series.get("dates") or []
        last_seen = _iso(dates[-1]) if dates else None

        area_ha = props.get("area_ha")
        if area_ha is None and geom:
            m2 = dl.geojson_area_m2(geom)
            area_ha = round(m2 / 10000.0, 2) if m2 else None

        rows.append({
            "name": name,
            "crop": crop,
            "crop_source": crop_source,
            "status": status["status"],
            "vigour": status["vigour"],
            "why": status["why"],
            "area_ha": area_ha,
            "geometry": geom or None,
            "centroid": dl.geojson_centroid(geom) if geom else None,
            "sown_date": sown,
            "greenup_date": greenup,
            "greenup_source": "SATELLITE" if greenup else None,
            "harvest_date": harvest_recorded or harvest_expected,
            "harvest_source": ("REPORTED" if harvest_recorded
                               else ("ESTIMATED" if harvest_expected else None)),
            "harvested": bool(harvest_recorded),
            "last_seen": last_seen,
            "record": rec,
        })
    return rows


def crops_in(index: list) -> list:
    """Crops present, for the filter menu. Sorted so the menu is stable."""
    return sorted({r["crop"] for r in index if r.get("crop")})


def status_counts(index: list) -> dict:
    out = {"attention": 0, "watch": 0, "ok": 0, "unmeasured": 0}
    for r in index:
        if r["status"] in out:
            out[r["status"]] += 1
    return out


# ==============================================================================
# THE FILTER
# ==============================================================================

DATE_FIELDS = ("greenup_date", "harvest_date", "last_seen", "sown_date")


def filter_fields(index: list, text: str = "", crops=None, statuses=None,
                  date_field: str = "greenup_date", date_from=None,
                  date_to=None, harvest: Optional[str] = None,
                  polygon: Optional[dict] = None) -> dict:
    """
    Apply every active filter and report three outcomes, not two.

    `harvest` is one of None (no filter), "harvested", "not_reported".

    Returns {matched, unknown, n_total, n_matched, n_unknown, active} where
    `unknown` holds fields set aside because the attribute being filtered on
    has no value for them, each carrying the reason. A field never disappears
    without an accounting.
    """
    crops = [c for c in (crops or []) if c]
    statuses = [s for s in (statuses or []) if s]
    date_from, date_to = _iso(date_from), _iso(date_to)
    q = normalise(text)

    active = []
    for flag, nm in ((q, "text"), (crops, "crop"), (statuses, "status"),
                     (date_from or date_to, "date"), (harvest, "harvest"),
                     (polygon, "area")):
        if flag:
            active.append(nm)

    matched, unknown = [], []
    for r in index:
        # Text matches the name or the crop. Never a reason to call a field
        # "unknown": an empty name is a match failure, not missing data.
        if q and q not in normalise(r.get("name")) \
                and q not in normalise(r.get("crop")):
            continue

        if statuses and r["status"] not in statuses:
            continue

        if crops:
            if not r.get("crop"):
                unknown.append({**r, "unknown_because": "crop"})
                continue
            if r["crop"] not in crops:
                continue

        if date_from or date_to:
            value = r.get(date_field)
            if not value:
                unknown.append({**r, "unknown_because": date_field})
                continue
            if date_from and value < date_from:
                continue
            if date_to and value > date_to:
                continue

        if harvest:
            # There is no satellite measurement of "this field has been cut",
            # and an EXPECTED harvest date is this tool's own arithmetic. So
            # the only positive evidence is a harvest the farmer reported.
            # Everything else is unknown - which is why the other option is
            # called "no harvest reported" and not "standing": a field cut
            # last week and never written down would sit in it.
            if harvest == "harvested" and not r["harvested"]:
                unknown.append({**r, "unknown_because": "harvest"})
                continue
            if harvest == "not_reported" and r["harvested"]:
                continue

        if polygon:
            c = r.get("centroid")
            if not c:
                unknown.append({**r, "unknown_because": "geometry"})
                continue
            if not dl.point_in_geometry(c, polygon):
                continue

        matched.append(r)

    return {"matched": matched, "unknown": unknown,
            "n_total": len(index), "n_matched": len(matched),
            "n_unknown": len(unknown), "active": active}


def field_at_point(index: list, lat, lon) -> Optional[str]:
    """Which field was clicked on the map. None if the click missed them all -
    a click on bare ground must not select the nearest field, because "nearest"
    quietly becomes "wrong" at the edge of a scheme."""
    if lat is None or lon is None:
        return None
    for r in index:
        if r.get("geometry") and dl.point_in_geometry([lon, lat], r["geometry"]):
            return r["name"]
    return None
