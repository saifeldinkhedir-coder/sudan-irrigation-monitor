"""
The AGRICULTURE engine - a farm monitoring system that stands on its own.

WHY THIS IS A SEPARATE ENGINE AND NOT A MODE OF THE OTHER ONE
--------------------------------------------------------------
engine.py is canal-centric. It starts from a canal, resolves the command area
that canal serves, and treats fields as an optional add-on inside it. That is the
right shape for the question "did water arrive and was it shared fairly", and it
is the wrong shape for a farmer, who has fields and no canal geometry, may not be
in a gravity scheme at all, and whose question is "how is my crop and what should
I do this week".

Running the network engine with an empty canal list would not produce this. The
whole chain - reference areas derived from command polygons, head-to-tail
reaches, canal water - is built around geometry a farmer does not have and should
not be asked for. So this is a separate entry point with a separate input
contract: fields in, farm report out. It imports nothing from engine.py.

WHAT IT DOES THAT COMMERCIAL FARM APPS DO
-----------------------------------------
Field mapping, crop health, canopy moisture, nutrition, weather, water need,
advisory, records, scouting. The same list, and the same shape of answer.

WHERE IT DIFFERS, AND WHY THAT IS NOT MARKETING
------------------------------------------------
Ten data sources against the one or two a typical app uses, and every one of them
earns its place by answering something the others cannot:

    Sentinel-2          vigour, canopy moisture, irrigated extent
    Sentinel-2 red-edge chlorophyll without saturating over a dense canopy,
                        which is exactly where nitrogen questions arise
    Landsat 8/9 thermal a DIRECT stress measure - a transpiring crop cools
                        itself - that moves days before NDVI does
    Sentinel-1 radar    cloud- and dust-proof observation
    CHIRPS              rainfall, which separates drought from everything else
    ERA5-Land           the FAO-56 inputs, growing degree days, heat stress
    MODIS               actual evapotranspiration at command scale
    NOAA GFS            the short outlook
    OpenLandMap         soil texture, which explains why two fields differ
    GRACE-FO            regional storage context ONLY - never field scale

More sensors is not automatically better. What makes it better here is that each
one is reported at the scale it can support and refused at the scale it cannot -
thermal at 100 m is a large-field measure, GRACE at 300 km is a regional one, and
neither is allowed to masquerade as a field-scale number.

THE FARMER'S ACTUAL QUESTION
----------------------------
Not "what is my NDVI". It is "which of my fields needs me first, and why". So the
farm report ranks fields against each other and states what is driving the rank -
which is a comparison this engine can make honestly, because all the fields were
measured the same way on the same dates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import decision_logic as dl

try:
    import ee
except ImportError:
    print("ABORT: earthengine-api not installed.  pip install earthengine-api")
    ee = None


# ==============================================================================
# CONFIGURATION - every hand-chosen number, declared
# ==============================================================================

SCALE_M = 10                 # Sentinel-2 native
SCALE_COARSE_M = 100         # Landsat thermal
CLOUD_PCT = 40
K_SIGMA = 2.0                # stress threshold multiplier

# A field's stress threshold needs a surrounding population. With no scheme
# geometry, the neighbourhood is a buffer around the field. ARBITRARY: 3 km is
# wide enough to contain other fields and narrow enough to stay in the same
# soil and climate.
NEIGHBOURHOOD_BUFFER_M = 3000

# Minimum usable observations before an indicator is reported at all.
MIN_S2_SCENES = 3
MIN_THERMAL_SCENES = 1

# Phenology. All ARBITRARY: the half-amplitude green-up convention is
# common and carries no physical claim, and the amplitude floor is the
# point below which a series has no season in it to find.
MIN_SCENES_FOR_PHENOLOGY = 8
PHENOLOGY_GREENUP_FRACTION = 0.5
PHENOLOGY_MIN_AMPLITUDE = 0.05

# Rainfall window for the cause-separating context.
RAIN_WINDOW_DAYS = 14
RAIN_LOW_MM = 5.0            # ARBITRARY

# Native pixel sizes of the coarse datasets. A region smaller than one of
# these must be buffered before it is reduced, or reduceRegion finds no
# pixel centre and returns a silent None.
CHIRPS_NATIVE_M = 5500

_S2 = "COPERNICUS/S2_SR_HARMONIZED"
_CHIRPS = "UCSB-CHG/CHIRPS/DAILY"
_SOIL = "OpenLandMap/SOL/SOL_TEXTURE-CLASS_USDA-TT_M/v02"


# ==============================================================================
# SHARED PLUMBING
# ==============================================================================

def init_ee() -> str:
    import os
    project = os.environ.get("EE_PROJECT")
    if not project:
        print("ABORT: set EE_PROJECT in the environment.")
        raise SystemExit(1)
    ee.Initialize(project=project)
    return project


def season_window(season: int) -> tuple:
    """July to March - the Sudanese cropping year, not a calendar year."""
    return f"{season}-07-01", f"{season + 1}-03-31"


def s2_collection(aoi, start: str, end: str):
    return (ee.ImageCollection(_S2).filterBounds(aoi).filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CLOUD_PCT)))


def s2_indices(img):
    """The optical index set, from scaled reflectance."""
    b = img.divide(10000)
    ndvi = b.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndmi = b.normalizedDifference(["B8", "B11"]).rename("NDMI")
    evi = b.expression(
        "2.5 * ((N - R) / (N + 6 * R - 7.5 * B + 1))",
        {"N": b.select("B8"), "R": b.select("B4"), "B": b.select("B2")}
    ).rename("EVI")
    return ee.Image.cat([ndvi, ndmi, evi])


def thermal_collection(aoi, start: str, end: str):
    """Landsat 8 and 9 surface temperature, in Celsius."""
    def to_lst(img):
        lst = img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15)
        return lst.rename("LST").copyProperties(img, ["system:time_start"])

    l8 = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
          .filterBounds(aoi).filterDate(start, end)
          .filter(ee.Filter.lt("CLOUD_COVER", 60)))
    l9 = (ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
          .filterBounds(aoi).filterDate(start, end)
          .filter(ee.Filter.lt("CLOUD_COVER", 60)))
    return l8.merge(l9).map(to_lst)


# ==============================================================================
# RESULT TYPE
# ==============================================================================

@dataclass
class Reading:
    """One measured quantity, with everything needed to judge it."""
    name: str
    status: str                       # OK | NOT AVAILABLE | INSUFFICIENT DATA
    value: Optional[float] = None
    unit: str = ""
    reason: Optional[str] = None
    threshold: Optional[float] = None
    threshold_basis: Optional[str] = None
    interpretation: str = ""
    sensor: str = ""
    n_scenes: Optional[int] = None
    scale_m: Optional[int] = None

    @staticmethod
    def unavailable(name, reason, sensor="", n_scenes=None):
        return Reading(name=name, status="NOT AVAILABLE", reason=reason,
                       sensor=sensor, n_scenes=n_scenes)


# ==============================================================================
# THE NEIGHBOURHOOD - what a field is compared against
# ==============================================================================

def neighbourhood_for(field_feature: dict, field_geom,
                      all_fields_geom=None) -> tuple:
    """
    Return (reference geometry, provenance) for a field's stress threshold.

    A field cannot be its own reference: its mean is never below its own
    median minus two sigma, so a threshold derived from it can never fire and
    "not stressed" is decided before any pixel is read. With no scheme geometry
    to fall back on, the reference is a buffer around the field - the
    surrounding landscape, which is what a farmer implicitly compares against
    anyway when they say a field looks worse than the ones around it.

    The buffer is checked against the same 10x area rule the scheme engine
    uses, so a very large field still gets a refusal rather than a threshold it
    dominates.
    """
    field_area = dl.geojson_area_m2(field_feature.get("geometry"))
    ref = field_geom.buffer(NEIGHBOURHOOD_BUFFER_M)

    # Buffer area is computed from the geometry we built, not measured on the
    # server: one fewer round trip, and the ratio only needs to be approximate.
    if field_area:
        import math
        r = NEIGHBOURHOOD_BUFFER_M
        approx_ref_area = field_area + 2 * math.sqrt(math.pi * field_area) * r \
            + math.pi * r * r
        adq = dl.reference_adequate(field_area, approx_ref_area)
        if not adq["ok"]:
            return None, {
                "reference_source": ("NOT AVAILABLE: this field is too large "
                                     "relative to its neighbourhood buffer for "
                                     "the buffer to be an independent "
                                     "population"),
                "field_area_ha": round(field_area / 10000.0, 2),
                "rejected": adq["reason"],
                "verdict_withheld": True}
        return ref, {
            "reference_source": (f"NEIGHBOURHOOD: {NEIGHBOURHOOD_BUFFER_M} m "
                                 "buffer around the field"),
            "field_area_ha": round(field_area / 10000.0, 2),
            "area_ratio": adq["ratio"],
            "buffer_basis": ("ARBITRARY: 3 km is wide enough to contain other "
                             "fields and narrow enough to stay in the same soil "
                             "and climate"),
            "verdict_withheld": False}

    return None, {"reference_source": "NOT AVAILABLE: field area could not be "
                                      "computed from its geometry",
                  "verdict_withheld": True}


# ==============================================================================
# CROP HEALTH
# ==============================================================================

def crop_health(field_geom, reference_geom, start: str, end: str) -> dict:
    """
    Vigour, canopy moisture and greenness, each with a threshold derived from
    the surrounding neighbourhood rather than from a fixed number.

    NDMI is listed second but matters first: canopy water falls before visible
    vigour does, so it is the earlier of the two warnings. A farmer acting on
    NDVI alone is acting on a signal that arrives after the damage.
    """
    out = {"readings": {}}
    col = s2_collection(field_geom, start, end)
    n = col.size().getInfo()
    if n < MIN_S2_SCENES:
        for name in ("vigour", "canopy_moisture", "greenness"):
            out["readings"][name] = asdict(Reading.unavailable(
                name, f"only {n} Sentinel-2 scenes; {MIN_S2_SCENES} needed",
                "Sentinel-2", n))
        return out

    idx = s2_indices(col.median())
    ref_idx = (s2_indices(s2_collection(reference_geom, start, end).median())
               if reference_geom is not None else None)

    for band, name, unit, interp in (
        ("NDVI", "vigour", "",
         "Crop vigour. The headline health number, and the slowest to move."),
        ("NDMI", "canopy_moisture", "",
         "Canopy water content. Falls BEFORE visible vigour does, so it is the "
         "earlier of the two warnings."),
        ("EVI", "greenness", "",
         "Enhanced vegetation index. Less prone to saturating over a dense "
         "canopy than NDVI."),
    ):
        v = idx.select(band).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=field_geom, scale=SCALE_M,
            maxPixels=1e9, bestEffort=True).getInfo().get(band)
        if v is None:
            out["readings"][name] = asdict(Reading.unavailable(
                name, "no valid pixels over this field", "Sentinel-2", n))
            continue

        thr, basis = None, ("NOT AVAILABLE: no neighbourhood, so no relative "
                            "threshold (a field vs itself can never flag)")
        if ref_idx is not None:
            p = ref_idx.select(band).reduceRegion(
                reducer=ee.Reducer.percentile([16, 50, 84]),
                geometry=reference_geom, scale=SCALE_M * 2,
                maxPixels=1e9, bestEffort=True).getInfo()
            thr = dl.robust_threshold(p.get(f"{band}_p16"), p.get(f"{band}_p50"),
                                      p.get(f"{band}_p84"), K_SIGMA,
                                      low_tail=True)
            basis = (f"DERIVED: median - {K_SIGMA} * robust sigma over the "
                     "neighbourhood, computed this run")

        out["readings"][name] = asdict(Reading(
            name=name, status="OK", value=round(v, 4), unit=unit,
            threshold=None if thr is None else round(thr, 4),
            threshold_basis=basis, interpretation=interp,
            sensor="Sentinel-2 median", n_scenes=n, scale_m=SCALE_M))
    return out


def thermal_stress(field_geom, reference_geom, start: str, end: str) -> dict:
    """
    Land surface temperature - a DIRECT physical stress measure, not a proxy.

    A transpiring crop cools itself; a water-stressed one heats up, and does so
    days before its canopy visibly changes. That makes thermal the earliest
    signal available here.

    100 m resolution. On a field smaller than about 2 hectares every pixel
    mixes the crop with its surroundings, so the figure is reported with the
    field's size beside it rather than silently.
    """
    col = thermal_collection(field_geom, start, end)
    n = col.size().getInfo()
    if n < MIN_THERMAL_SCENES:
        return {"status": "NOT AVAILABLE",
                "reason": ("no cloud-free Landsat thermal scenes over this "
                           "field in the window. Landsat revisits every 16 "
                           "days, so a cloudy season can genuinely produce "
                           "none."),
                "sensor": "Landsat 8/9 ST_B10", "n_scenes": n}

    v = col.median().reduceRegion(
        reducer=ee.Reducer.mean(), geometry=field_geom, scale=SCALE_COARSE_M,
        maxPixels=1e9, bestEffort=True).getInfo().get("LST")
    if v is None:
        return {"status": "NOT AVAILABLE", "reason": "no valid thermal pixels",
                "sensor": "Landsat 8/9 ST_B10", "n_scenes": n}

    out = {"status": "OK", "value": round(v, 2), "unit": "degC",
           "sensor": "Landsat 8/9 ST_B10", "n_scenes": n,
           "scale_m": SCALE_COARSE_M,
           "interpretation": ("Land surface temperature. A transpiring crop is "
                              "cooler than a stressed one, so a field warm "
                              "relative to its neighbours is a direct "
                              "water-stress signal - and it moves days before "
                              "vigour does.")}

    if reference_geom is not None:
        rv = thermal_collection(reference_geom, start, end).median().reduceRegion(
            reducer=ee.Reducer.mean(), geometry=reference_geom,
            scale=SCALE_COARSE_M, maxPixels=1e9,
            bestEffort=True).getInfo().get("LST")
        if rv is not None:
            out["neighbourhood_c"] = round(rv, 2)
            out["difference_c"] = round(v - rv, 2)
            out["reading"] = (
                "warmer than the surrounding land" if v - rv > 1.0
                else "cooler than the surrounding land" if v - rv < -1.0
                else "close to the surrounding land")
            out["difference_basis"] = ("ARBITRARY: 1 degC is the band treated "
                                       "as indistinguishable")
    return out


# ==============================================================================
# RAINFALL CONTEXT  (never report stress without it)
# ==============================================================================

def rainfall_context(field_geom, start: str, end: str) -> dict:
    """
    Rain over the whole season and over the last fortnight.

    This is the dataset that separates "stressed because it did not rain" from
    "stressed although it rained". Reporting the first as a crop problem wastes
    a farmer's day, and doing it repeatedly destroys trust in every later alert.
    """
    # CHIRPS is ~5.5 km. A field is far smaller, so the region is buffered to
    # one native pixel: without it reduceRegion finds no pixel centre inside
    # the polygon and returns None, which then reads as "no rainfall data" for
    # a dataset that covers the field perfectly well.
    region = field_geom.buffer(dl.coarse_sampling_buffer_m(CHIRPS_NATIVE_M))

    def total(a, b):
        try:
            col = ee.ImageCollection(_CHIRPS).filterBounds(region).filterDate(a, b)
            if col.size().getInfo() == 0:
                return None
            v = col.sum().reduceRegion(
                reducer=ee.Reducer.mean(), geometry=region,
                scale=CHIRPS_NATIVE_M, maxPixels=1e9,
                bestEffort=True).getInfo()
            return v.get("precipitation")
        except Exception:
            return None

    end_dt = datetime.strptime(end, "%Y-%m-%d")
    recent_start = (end_dt - timedelta(days=RAIN_WINDOW_DAYS)).strftime("%Y-%m-%d")
    season_mm = total(start, end)
    recent_mm = total(recent_start, end)
    return {
        "season_mm": round(season_mm, 1) if season_mm is not None else None,
        "last_14d_mm": round(recent_mm, 1) if recent_mm is not None else None,
        "rain_floor_mm": RAIN_LOW_MM,
        "rain_floor_basis": "ARBITRARY",
        "sensor": "CHIRPS daily",
        "sampled_as": (f"the CHIRPS cell containing this field ({CHIRPS_NATIVE_M} m); "
                       "rainfall is not resolved at field scale by any dataset here"),
        "why": ("Without rainfall context, a stressed field cannot be told "
                "apart from a dry season. The two need different actions."),
    }


# ==============================================================================
# SOIL - the standing explanation for why two fields differ
# ==============================================================================

def soil_texture(field_geom) -> dict:
    """
    USDA texture class under the field.

    Not a live measurement and not a soil test: a global model at 250 m. It is
    here because it is the most common honest answer to "why is that field
    always worse than this one", and because a farmer who knows one field is
    heavier clay reads every other number differently.
    """
    try:
        v = ee.Image(_SOIL).reduceRegion(
            reducer=ee.Reducer.mode(), geometry=field_geom, scale=250,
            maxPixels=1e9, bestEffort=True).getInfo().get("b0")
        if v is None:
            return {"status": "NOT AVAILABLE", "reason": "no soil pixels"}
        classes = {1: "clay", 2: "silty clay", 3: "sandy clay",
                   4: "clay loam", 5: "silty clay loam", 6: "sandy clay loam",
                   7: "loam", 8: "silt loam", 9: "sandy loam",
                   10: "silt", 11: "loamy sand", 12: "sand"}
        return {"status": "OK", "class_code": int(v),
                "texture": classes.get(int(v), "unknown"),
                "sensor": "OpenLandMap USDA texture class, 250 m",
                "caveat": ("A global model, not a soil test. Useful for "
                           "explaining differences between fields; not a "
                           "substitute for sampling.")}
    except Exception as e:
        return {"status": "NOT AVAILABLE", "reason": str(e)[:120]}


# ==============================================================================
# TIME SERIES - the trend a farmer actually watches
# ==============================================================================

def health_series(field_geom, start: str, end: str) -> dict:
    """
    Per-scene NDVI and NDMI with dates, in one round trip.

    A single seasonal median hides the shape of the season. A farmer watching a
    curve sees a stall, a drop, or a late start; a farmer handed one number
    sees none of them.
    """
    try:
        col = s2_collection(field_geom, start, end)
        n = col.size().getInfo()
        if n == 0:
            return {"status": "NOT AVAILABLE", "reason": "no Sentinel-2 scenes"}

        def one(img):
            i = s2_indices(img)
            stats = i.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=field_geom, scale=SCALE_M,
                maxPixels=1e9, bestEffort=True)
            return ee.Feature(None, {"ndvi": stats.get("NDVI"),
                                     "ndmi": stats.get("NDMI"),
                                     "t": img.date().millis()})

        fc = ee.FeatureCollection(col.map(one))
        ndvi = fc.aggregate_array("ndvi").getInfo()
        ndmi = fc.aggregate_array("ndmi").getInfo()
        ts = fc.aggregate_array("t").getInfo()
        dates = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")
                 for t in ts]
        pairs = sorted(zip(dates, ndvi, ndmi))
        return {"status": "OK", "n_scenes": n,
                "dates": [p[0] for p in pairs],
                "ndvi": [None if p[1] is None else round(p[1], 4) for p in pairs],
                "ndmi": [None if p[2] is None else round(p[2], 4) for p in pairs],
                "note": ("Cloud-screened scenes only. Gaps in the series are "
                         "cloud, not crop failure.")}
    except Exception as e:
        return {"status": "NOT AVAILABLE", "reason": str(e)[:140]}


def _series_day_offsets(series: dict, start: str):
    """Turn the dated NDVI series into (day offsets from season start, values).

    Returns (None, None) when there is no usable series, so the caller falls
    back to the labelled approximate ETc rather than silently passing empty
    lists into the integral."""
    if not series or series.get("status") != "OK":
        return None, None
    s0 = datetime.strptime(start, "%Y-%m-%d")
    days, values = [], []
    for d, v in zip(series.get("dates", []), series.get("ndvi", [])):
        if v is None:
            continue
        days.append((datetime.strptime(d, "%Y-%m-%d") - s0).days)
        values.append(v)
    return (days, values) if len(days) >= 2 else (None, None)


def phenology(days, ndvi) -> dict:
    """
    When the crop greened up, when it peaked, and how long it lasted.

    For a farmer this is often more useful than how green it got: a late
    green-up or a short season explains a poor result in a way a single vigour
    number does not, and it is the figure that separates "planted late" from
    "planted on time and then starved".

    Refuses on a series too short or too flat to contain a season, rather than
    reporting the day of the highest value in a noisy series as if it meant
    something.
    """
    pts = sorted((float(d), float(v)) for d, v in zip(days, ndvi) if v is not None)
    if len(pts) < MIN_SCENES_FOR_PHENOLOGY:
        return {"status": "NOT AVAILABLE",
                "reason": (f"only {len(pts)} usable scenes; "
                           f"{MIN_SCENES_FOR_PHENOLOGY} needed before a "
                           "green-up day means anything"),
                "basis": "ARBITRARY minimum"}
    vs = [v for _, v in pts]
    lo, hi = min(vs), max(vs)
    amplitude = hi - lo
    if amplitude < PHENOLOGY_MIN_AMPLITUDE:
        return {"status": "NOT AVAILABLE",
                "amplitude": round(amplitude, 4),
                "reason": (f"seasonal NDVI amplitude {round(amplitude, 3)} is too "
                           "flat to contain a green-up. That is a statement "
                           "about this field's vegetation, not a data failure - "
                           "it may simply not have been cropped."),
                "basis": f"ARBITRARY floor of {PHENOLOGY_MIN_AMPLITUDE}"}

    target = lo + PHENOLOGY_GREENUP_FRACTION * amplitude
    greenup = next((d1 for (d0, v0), (d1, v1) in zip(pts, pts[1:])
                    if v0 < target <= v1), None)
    peak_day, peak_val = max(pts, key=lambda p: p[1])
    above = [d for d, v in pts if v >= target]
    return {
        "status": "OK",
        "greenup_day": greenup,
        "peak_day": peak_day,
        "peak_ndvi": round(peak_val, 4),
        "season_length_days": (max(above) - min(above)) if len(above) >= 2 else None,
        "amplitude": round(amplitude, 4),
        "n_scenes": len(pts),
        "basis": (f"ARBITRARY: green-up is the first crossing of "
                  f"{PHENOLOGY_GREENUP_FRACTION} of the seasonal amplitude. "
                  "Days are counted from the start of the season window, and "
                  "cloud gaps mean the true crossing may fall earlier than the "
                  "first scene that shows it."),
    }


# ==============================================================================
# THE FARMER'S QUESTION: WHICH FIELD FIRST
# ==============================================================================

def rank_fields(field_records: list) -> dict:
    """
    Order the farm's fields by how much they need attention, and say what drove
    each position.

    WHY RANKING RATHER THAN SCORING
    An absolute "health score out of 100" implies a calibrated scale that does
    not exist. A ranking only claims that these fields, measured the same way on
    the same dates, differ - which is exactly what the data supports and exactly
    the decision a farmer makes: where to walk first.

    Fields whose vigour could not be measured are listed separately rather than
    ranked last. Unmeasured is not healthy and it is not sick.
    """
    ranked, unmeasured = [], []
    for rec in field_records:
        readings = (rec.get("crop_health") or {}).get("readings", {})
        vig = readings.get("vigour", {})
        moist = readings.get("canopy_moisture", {})
        if vig.get("status") != "OK" or vig.get("value") is None:
            unmeasured.append({"name": rec.get("name"),
                               "reason": vig.get("reason", "vigour not measured")})
            continue

        drivers = []
        # Below the neighbourhood threshold is the strongest single signal.
        below_threshold = (vig.get("threshold") is not None
                           and vig["value"] < vig["threshold"])
        if below_threshold:
            drivers.append("vigour below the neighbourhood threshold")
        if (moist.get("status") == "OK" and moist.get("threshold") is not None
                and moist.get("value") is not None
                and moist["value"] < moist["threshold"]):
            drivers.append("canopy moisture below the neighbourhood threshold")
        th = rec.get("thermal_stress") or {}
        if th.get("status") == "OK" and (th.get("difference_c") or 0) > 1.0:
            drivers.append(f"{th['difference_c']} degC warmer than its surroundings")
        wr = rec.get("water_requirement") or {}
        if wr.get("status") == "OK" and wr.get("irrigation_requirement_mm"):
            drivers.append(f"{wr['irrigation_requirement_mm']:.0f} mm of water "
                           "needed beyond rainfall")

        ranked.append({
            "name": rec.get("name"),
            "vigour": vig["value"],
            "below_threshold": below_threshold,
            "drivers": drivers,
            # Sort key: flagged fields first, then by vigour ascending. The key
            # is an ordering device, NOT a score, and is not reported as one.
            "_sort": (not below_threshold, vig["value"]),
        })

    ranked.sort(key=lambda r: r["_sort"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
        del r["_sort"]

    return {
        "ranked": ranked,
        "unmeasured": unmeasured,
        "basis": ("Fields ordered by whether vigour fell below the "
                  "neighbourhood threshold, then by vigour. This is an "
                  "ordering, not a score: no calibrated health scale exists, "
                  "and one is not invented here."),
        "unmeasured_note": ("Fields with no usable vigour reading are listed "
                            "separately, not ranked last. Unmeasured is "
                            "neither healthy nor sick."),
    }


# ==============================================================================
# ASSEMBLY
# ==============================================================================

def analyse_farm(field_fc: dict, season: int, out_json: str,
                 crop: str = "default", with_series: bool = True) -> dict:
    """
    Full farm report from field polygons alone. No canal geometry, no command
    areas, no scheme.
    """
    proj = init_ee()
    start, end = season_window(season)
    feats = field_fc.get("features", [])

    print("=" * 72)
    print("Farm Monitor - agriculture engine")
    print("=" * 72)
    print(f"Season  : {start} to {end}")
    print(f"Project : {proj}")
    print(f"Fields  : {len(feats)}")
    print(f"Crop    : {crop}")

    try:
        import nutrition_climate_ground as ncg
    except Exception as e:
        print(f"  (nutrition/climate unavailable: {e})")
        ncg = None
    try:
        import agronomy as agro
    except Exception as e:
        print(f"  (agronomy unavailable: {e})")
        agro = None
    try:
        import farm_records as fr
    except Exception:
        fr = None

    if not feats:
        print("\nNo fields supplied. There is nothing to report on, and no "
              "field boundary can honestly be invented.")
        return {"fields": [], "n_fields": 0}

    all_geom = ee.FeatureCollection(
        [ee.Feature(ee.Geometry(f["geometry"])) for f in feats]).geometry()

    results = {
        "tool": "Farm Monitor - agriculture engine",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "gee_project": proj,
        "season": {"start": start, "end": end},
        "crop": crop,
        "n_fields": len(feats),
        "sensors": {
            "Sentinel-2": "10 m optical - vigour, canopy moisture, greenness",
            "Sentinel-2 red-edge": "B5-B7 - chlorophyll without saturating",
            "Landsat 8/9 thermal": "100 m LST - direct water-stress measure",
            "CHIRPS": "daily rainfall - separates drought from crop problems",
            "ERA5-Land": "FAO-56 inputs, growing degree days, heat stress",
            "MODIS": "actual evapotranspiration",
            "NOAA GFS": "7-day outlook, ~28 km - scheme scale, not field scale",
            "OpenLandMap": "soil texture, 250 m - explains standing differences",
        },
        "fields": [],
        "limitations": [
            "Thermal is 100 m: on a field under about 2 ha every pixel mixes "
            "the crop with its surroundings.",
            "Nutrition is a RELATIVE chlorophyll condition unless a calibrated "
            "model exists for this crop. Nitrogen has no direct spectral "
            "signature; water stress, salinity, disease and heat all lower "
            "chlorophyll too.",
            "Crop water requirement is what the crop NEEDED. Nothing here "
            "measures what it received.",
            "Yield is refused without local harvest calibration.",
            "The 7-day outlook is a ~28 km global model: the weather over the "
            "area, not over a field. No alert is raised from it.",
            "Soil texture is a global model at 250 m, not a soil test.",
            "Field rankings are an ordering, not a calibrated health score.",
        ],
    }

    scheme_stats = {}
    if ncg is not None:
        try:
            scol = s2_collection(all_geom, start, end)
            if scol.size().getInfo() >= MIN_S2_SCENES:
                cidx = ncg.chlorophyll_indices(scol.median())
                ps = cidx.select("CIre").reduceRegion(
                    reducer=ee.Reducer.percentile([25, 75]), geometry=all_geom,
                    scale=SCALE_M * 2, maxPixels=1e9, bestEffort=True).getInfo()
                scheme_stats = {"cire_p25": ps.get("CIre_p25"),
                                "cire_p75": ps.get("CIre_p75")}
        except Exception as e:
            print(f"  (farm-wide nutrition stats unavailable: {e})")

    for i, f in enumerate(feats, 1):
        name = f.get("properties", {}).get("name", f"field_{i}")
        geom = ee.Geometry(f["geometry"])
        print(f"\n[{i}/{len(feats)}] {name}")

        ref, ref_prov = neighbourhood_for(f, geom)
        print(f"  reference        : {ref_prov['reference_source'].split(':')[0]}")

        health = crop_health(geom, ref, start, end)
        vig = health["readings"].get("vigour", {})
        print(f"  vigour           : {vig.get('status')}"
              + (f"  {vig.get('value')}" if vig.get("value") is not None else ""))

        rec = {
            "name": name,
            "properties": f.get("properties", {}),
            "reference_provenance": ref_prov,
            "crop_health": health,
            "thermal_stress": thermal_stress(geom, ref, start, end),
            "rainfall": rainfall_context(geom, start, end),
            "soil": soil_texture(geom),
        }
        # The dated canopy series is computed first because the water
        # requirement needs it: ETc is the daily sum of Kcb(t) * ET0(t), and
        # without dates that integral collapses to the biased season-mean
        # shortcut it exists to replace.
        series = health_series(geom, start, end) if with_series else {}
        if with_series:
            rec["series"] = series

        if agro is not None:
            v = vig.get("value") if vig.get("status") == "OK" else None
            days, values = _series_day_offsets(series, start)
            try:
                rec["water_requirement"] = agro.crop_water_requirement(
                    geom, start, end, v,
                    ndvi_days=days, ndvi_values=values)
            except Exception as e:
                rec["water_requirement"] = {"status": "NOT AVAILABLE",
                                            "reason": str(e)[:140]}
            rec["yield_estimate"] = agro.yield_estimate(v, crop)
            if days and values:
                rec["phenology"] = phenology(days, values)

        if ncg is not None:
            try:
                rec["climate"] = ncg.climate_context(geom, start, end, crop)
            except Exception as e:
                rec["climate"] = {"status": "NOT AVAILABLE", "reason": str(e)[:140]}
            try:
                ncol = s2_collection(geom, start, end)
                if ncol.size().getInfo() >= MIN_S2_SCENES:
                    cidx = ncg.chlorophyll_indices(ncol.median())
                    fvals = cidx.reduceRegion(
                        reducer=ee.Reducer.mean(), geometry=geom,
                        scale=SCALE_M, maxPixels=1e9, bestEffort=True).getInfo()
                    strip = f.get("properties", {}).get("reference_strip")
                    strip_vals = None
                    if strip:
                        sg = ee.Geometry(strip)
                        strip_vals = ncg.chlorophyll_indices(
                            s2_collection(sg, start, end).median()).reduceRegion(
                            reducer=ee.Reducer.mean(), geometry=sg,
                            scale=SCALE_M, maxPixels=1e9,
                            bestEffort=True).getInfo()
                    nres = ncg.nutrition_status(fvals, scheme_stats, crop,
                                                reference_indices=strip_vals)
                    rec["nutrition"] = ncg.asdict_nutrition(nres)
                else:
                    rec["nutrition"] = {"status": "NOT AVAILABLE",
                                        "reason": "too few Sentinel-2 scenes"}
            except Exception as e:
                rec["nutrition"] = {"status": "NOT AVAILABLE", "reason": str(e)[:140]}

        if fr is not None:
            rec["advisory"] = fr.advisory(rec, canal_record=None, lang="ar")
            rec["advisory_en"] = fr.advisory(rec, canal_record=None, lang="en")

        results["fields"].append(rec)

    results["ranking"] = rank_fields(results["fields"])
    if agro is not None:
        results["forecast"] = agro.forecast_7day(all_geom)

    print("\n" + "-" * 72)
    print("Attention order:")
    for r in results["ranking"]["ranked"]:
        flag = " *" if r["below_threshold"] else "  "
        print(f" {flag} {r['rank']}. {r['name']}  (vigour {r['vigour']})")
        for d in r["drivers"]:
            print(f"        - {d}")
    for u in results["ranking"]["unmeasured"]:
        print(f"    -  {u['name']}: {u['reason']}")

    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)
    print(f"\nWritten to {out_json}")
    return results
