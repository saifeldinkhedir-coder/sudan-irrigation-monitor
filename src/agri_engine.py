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
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import decision_logic as dl
import crops as cr
import disease as dz
import checkpoint as cp

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
# Below this many 100 m thermal pixels across, a field and its
# neighbourhood sample much the same ground. ARBITRARY.
MIN_THERMAL_PIXELS_ACROSS = 2.0

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


def thermal_stress(field_geom, reference_geom, start: str, end: str,
                   field_feature_geometry: Optional[dict] = None) -> dict:
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

    # How many thermal pixels does this field actually span? Below about two,
    # every pixel mixes the crop with its surroundings, so the field and its
    # neighbourhood are sampling much the same ground and the difference
    # between them is forced toward zero by geometry rather than measured. That
    # is the same class of error as a stress threshold a field sets for itself,
    # and it is reported rather than left for the reader to work out.
    import math as _m
    side_m = _m.sqrt(dl.geojson_area_m2(field_feature_geometry) or 0.0)         if field_feature_geometry else None
    pixels = (side_m / SCALE_COARSE_M) if side_m else None

    out = {"status": "OK", "value": round(v, 2), "unit": "degC",
           "sensor": "Landsat 8/9 ST_B10", "n_scenes": n,
           "scale_m": SCALE_COARSE_M,
           "pixels_across": round(pixels, 1) if pixels else None,
           "resolvable": (None if pixels is None
                          else pixels >= MIN_THERMAL_PIXELS_ACROSS),
           "resolvability_note": (
               None if pixels is None else
               (f"about {pixels:.1f} thermal pixels across - the field and its "
                "surroundings are largely the same pixels, so any difference "
                "between them is suppressed by resolution rather than measured"
                if pixels < MIN_THERMAL_PIXELS_ACROSS else
                f"about {pixels:.1f} thermal pixels across - wide enough for "
                "the field and its surroundings to be distinguishable")),
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


# ==============================================================================
# WITHIN-FIELD ANOMALY - where in this field to walk
# ==============================================================================

def anomaly_scan(field_geom, field_geometry_dict: Optional[dict],
                 start: str, end: str) -> dict:
    """
    Find the part of a field that is unlike the rest of the field.

    THE REFERENCE IS THE FIELD ITSELF. Everything else in this engine compares
    a field with its neighbourhood; this compares a field with its own
    interior, which answers a different question - not "is this field worse
    than its neighbours" but "is one part of it worse than the rest". A
    uniformly poor field produces nothing here, and that is correct.

    It returns a size and a direction and NAMES NO CAUSE. Disease, a blocked
    outlet, salinity, pest damage and a badly set seed drill all look like this
    at 10 m, and the bands cannot separate them. See src/disease.py.
    """
    try:
        col = s2_collection(field_geom, start, end)
        if col.size().getInfo() < MIN_S2_SCENES:
            return {"status": "NOT AVAILABLE",
                    "reason": f"fewer than {MIN_S2_SCENES} usable Sentinel-2 "
                              "scenes over this field"}
        ndvi = col.median().normalizedDifference(["B8", "B4"]).rename("NDVI")

        pct = ndvi.reduceRegion(
            reducer=ee.Reducer.percentile([16, 50, 84]), geometry=field_geom,
            scale=SCALE_M, maxPixels=1e9, bestEffort=True).getInfo() or {}
        p16, p50, p84 = (pct.get("NDVI_p16"), pct.get("NDVI_p50"),
                         pct.get("NDVI_p84"))

        thr = dz.anomaly_threshold(p16, p50, p84)
        if thr["status"] != "OK":
            return {**thr, "distribution": {"p16": p16, "p50": p50,
                                            "p84": p84}}

        mask = ndvi.lt(thr["threshold"]).selfMask() if hasattr(ndvi, "selfMask") \
            else ndvi.lt(thr["threshold"])
        area_m2 = (ee.Image.pixelArea().updateMask(mask).reduceRegion(
            reducer=ee.Reducer.sum(), geometry=field_geom, scale=SCALE_M,
            maxPixels=1e9, bestEffort=True).getInfo() or {}).get("area")
        ll = (ee.Image.pixelLonLat().updateMask(mask).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=field_geom, scale=SCALE_M,
            maxPixels=1e9, bestEffort=True).getInfo() or {})

        field_m2 = dl.geojson_area_m2(field_geometry_dict)
        centre = dl.geojson_centroid(field_geometry_dict)
        patch = ([ll.get("longitude"), ll.get("latitude")]
                 if ll.get("longitude") is not None else None)

        # NO PIXEL MATCHED IS NOT THE SAME AS COULD NOT COMPUTE.
        #
        # A sum over a fully masked image returns null, not zero: Earth Engine
        # has nothing to add up. Passing that null through as "no anomaly area
        # computed" turned the healthiest possible answer - not one pixel in
        # this field is below its own threshold - into a failure message, on
        # two of the four fields in the first live run.
        #
        # The distribution above is what separates the two. If the percentiles
        # came back, the scan ran and saw the field; a null area then means
        # zero anomalous pixels. If the percentiles were null, nothing was
        # seen, and that is the branch above which has already returned.
        area_ha = (area_m2 / 10000.0) if area_m2 is not None else 0.0

        out = dz.anomaly_patch(
            area_ha, (field_m2 / 10000.0) if field_m2 else None,
            centre, patch)
        if area_m2 is None:
            out["reason"] = ("no pixel in this field is below its own "
                             "threshold")
            out["reason_ar"] = ("لا بكسل في هذا الحقل دون عتبته هو")
        out["threshold"] = thr["threshold"]
        out["distribution"] = {"p16": p16, "p50": p50, "p84": p84}
        out["robust_sigma"] = thr["robust_sigma"]
        out["basis"] = thr["basis"]
        out["sensor"] = "Sentinel-2 median NDVI"
        out["scale_m"] = SCALE_M
        return out
    except Exception as e:
        return {"status": "NOT AVAILABLE", "reason": str(e)[:140]}


def disease_layer(field_geom, start: str, end: str, crop: str,
                  anomaly: Optional[dict] = None,
                  scouting: Optional[list] = None, agro=None,
                  weather: Optional[dict] = None,
                  rain: Optional[list] = None) -> dict:
    """
    The three-rung disease and pest layer for one field.

    `weather` and `rain` are passed in when the farm fits inside one cell of
    the coarse datasets, so the same series is not fetched once per field. Rung
    1 is the anomaly scan, rung 2 the weather windows, and rung 3 whatever a
    human recorded - the only rung that names a disease.
    """
    risk = {"risks": [], "no_model": []}
    reason = None
    series = weather
    if series is None and agro is not None:
        series = agro.era5_daily_series(field_geom, start, end)
    if series:
        k = 273.15
        t_min = [None if v is None else v - k for v in series.get("t_min") or []]
        t_max = [None if v is None else v - k for v in series.get("t_max") or []]
        t_dew = [None if v is None else v - k for v in series.get("t_dew") or []]
        wet = rain if rain is not None else (
            daily_rain_mm(field_geom, start, end) or [])
        risk = dz.crop_risk(crop, t_min, t_max, t_dew, wet, start_date=start)
    elif agro is None:
        reason = "agronomy module unavailable, so no weather series"
    else:
        reason = "no ERA5-Land daily series, so no infection window"

    out = dz.diagnose(anomaly, risk, scouting, crop)
    out["crop"] = cr.resolve(crop)
    out["risk"] = risk
    out["refusal"] = dz.REFUSAL
    out["refusal_ar"] = dz.REFUSAL_AR
    if reason:
        out["risk_reason"] = reason
    return out


def farm_fits_one_cell(feats: list, native_m: float) -> dict:
    """
    Is the whole farm inside a single pixel of a coarse dataset?

    ERA5-Land is 11 km and CHIRPS is 5.5 km. A farm that fits inside one of
    those cells has ONE weather series, not one per field - and fetching it per
    field made the same round trip four times for four identical answers. On a
    forty-field scheme that is forty.

    But this is only true while the farm is small. A scheme spread over thirty
    kilometres spans several cells, and reusing one field's series for all of
    them would be inventing weather for the far end. So the extent is measured
    and the answer is reported, rather than assumed either way.
    """
    pts = []
    for f in feats:
        ring = ((f.get("geometry") or {}).get("coordinates") or [[]])[0]
        pts.extend(ring)
    if not pts:
        return {"fits": False, "reason": "no coordinates"}
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    # Degrees to metres at this latitude; good enough for a comparison against
    # an 11 km pixel, and it errs towards fetching per field.
    import math
    mid = math.radians(sum(lats) / len(lats))
    span_m = max((max(lons) - min(lons)) * 111320.0 * math.cos(mid),
                 (max(lats) - min(lats)) * 110540.0)
    fits = span_m <= native_m
    return {"fits": fits, "extent_m": round(span_m), "native_m": native_m,
            "reason": ("the farm fits inside one cell of this dataset, so one "
                       "series describes every field in it" if fits else
                       "the farm is wider than one cell, so each field gets "
                       "its own series")}


def daily_rain_mm(aoi, start: str, end: str) -> Optional[list]:
    """CHIRPS daily rainfall over the field, as a list in date order.

    Buffered to one native pixel: CHIRPS is 5.5 km and a field is ~600 m, so an
    unbuffered reduction encloses no pixel centre and returns nulls for a
    dataset that covers the field perfectly well.
    """
    try:
        region = aoi.buffer(dl.coarse_sampling_buffer_m(5566))
        col = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
               .filterBounds(region).filterDate(start, end))
        if col.size().getInfo() == 0:
            return None

        def day(img):
            v = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region,
                                 scale=5566, maxPixels=1e8, bestEffort=True)
            return ee.Feature(None, {"p": v.get("precipitation")})

        return ee.FeatureCollection(col.map(day)).aggregate_array("p").getInfo()
    except Exception:
        return None


def field_crop(feature: dict, run_crop: str) -> dict:
    """
    Which crop is standing in THIS field.

    The engine used to apply one crop to the whole run, so a wheat block inside
    a sorghum farm was given sorghum's growing-degree base and its 38 degC heat
    threshold - six degrees above where wheat starts losing grain. The number
    was not missing. It was wrong, and nothing said so.
    """
    declared = (feature.get("properties") or {}).get("crop")
    source = "field" if declared else ("run" if run_crop else None)
    info = cr.get(declared or run_crop)
    return {"key": info["key"], "declared": info["declared"], "source": source,
            "recognised": info["recognised"],
            "ar": info["ar"], "en": info["en"],
            "gdd_base_c": info["gdd_base_c"],
            "heat_stress_c": info["heat_stress_c"],
            "basis": info["basis"],
            "note": ("" if info["recognised"] else
                     f"the crop \"{info['declared']}\" is not in the crop "
                     "library, so generic parameters were used and every "
                     "crop-specific figure below rests on them"),
            "note_ar": ("" if info["recognised"] else
                        f"المحصول «{info['declared']}» ليس في مكتبة المحاصيل، "
                        "فاستُخدمت معاملات عامّة، وكل رقم يخصّ المحصول أدناه "
                        "يقوم عليها")}


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
                 crop: str = "default", with_series: bool = True,
                 observations_db: Optional[str] = None,
                 resume: bool = True, restart: bool = False) -> dict:
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

    # The ground-truth store, if the app has been collecting into one. It is
    # the only source that can name a disease, so its absence is stated rather
    # than left as a silently empty third rung.
    obs_store = None
    if observations_db and os.path.exists(observations_db) and ncg is not None:
        obs_store = ncg.ObservationStore(observations_db)
        print(f"Scouting: reading named findings from {observations_db}")
    elif observations_db:
        print(f"Scouting: {observations_db} not found - no field reports, so "
              "no disease can be named by this run")

    if not feats:
        print("\nNo fields supplied. There is nothing to report on, and no "
              "field boundary can honestly be invented.")
        return {"fields": [], "n_fields": 0}

    # A run over a scheme is thousands of round trips over a connection that
    # is not reliable. Losing 3,699 successful fields because the 3,700th timed
    # out is how a tool that could monitor the scheme ends up not monitoring
    # it, for reasons that have nothing to do with remote sensing.
    check = cp.Checkpoint(out_json,
                          cp.fingerprint(field_fc, season, crop, with_series),
                          enabled=resume)
    already = check.resume(restart=restart)
    if check.note:
        print(f"Checkpoint: {check.note}")

    all_geom = ee.FeatureCollection(
        [ee.Feature(ee.Geometry(f["geometry"])) for f in feats]).geometry()

    # ONE WEATHER SERIES FOR A FARM THAT FITS IN ONE WEATHER PIXEL.
    # ERA5-Land is 11 km. Fetching it per field made the same round trip once
    # per field for identical answers - forty times on a forty-field scheme.
    # Reused only while the farm genuinely fits inside one cell; a scheme
    # spread over thirty kilometres spans several, and sharing one series
    # across those would be inventing weather for the far end.
    ERA5_M, CHIRPS_M = 11132.0, 5566.0
    era5_fit = farm_fits_one_cell(feats, ERA5_M)
    chirps_fit = farm_fits_one_cell(feats, CHIRPS_M)
    farm_weather = farm_rain = None
    if era5_fit["fits"] and agro is not None:
        farm_weather = agro.era5_daily_series(all_geom, start, end)
        print(f"  weather          : one ERA5-Land series for the whole farm "
              f"({era5_fit['extent_m']} m across, {int(ERA5_M)} m cell)")
    if chirps_fit["fits"]:
        farm_rain = daily_rain_mm(all_geom, start, end)

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
            "NO DISEASE IS NAMED FROM SATELLITE IMAGERY. Disease, water "
            "stress, nitrogen deficiency, salinity, pest damage and lodging "
            "all move these bands together and cannot be separated by them. "
            "An anomaly says a patch is unlike the rest of the field and "
            "names no cause.",
            "Infection risk is weather favourability from published models, "
            "not validated in Sudan, and it describes the air over the area - "
            "it is equally true of every healthy field under that sky.",
            "Crop parameters are FAO-56 and conventional published figures, "
            "not Sudanese trial data. A heat threshold is as much a variety "
            "property as a species one.",
        ],
        # The same list in Arabic. Emitted by the engine rather than translated
        # in the app, for the reason every other vocabulary in this project is:
        # matching generated English afterwards fails silently the moment the
        # wording changes, and it fails by showing English to an Arabic reader.
        # This is the list of things the tool does NOT claim - the last list
        # that should reach a farmer in a language they may not read.
        "limitations_ar": [
            "الحرارة تُقاس عند 100 متر: في حقل دون هكتارين تقريبًا يخلط كل "
            "بكسل المحصولَ بما حوله.",
            "التغذية حالة كلوروفيل نسبية ما لم يوجد نموذج معايَر لهذا المحصول. "
            "وللنيتروجين لا بصمة طيفية مباشرة؛ فإجهاد الماء والملوحة والمرض "
            "والحرارة تخفض الكلوروفيل كذلك.",
            "احتياج المحصول من الماء هو ما احتاجه المحصول. ولا شيء هنا يقيس "
            "ما وصله فعلًا.",
            "الإنتاجية مرفوضة دون معايرة حصاد محلية.",
            "توقّعات الأيام السبعة نموذج عالمي بدقّة نحو 28 كم: طقس المنطقة "
            "لا طقس الحقل. ولا يُرفع منها أي إنذار.",
            "قوام التربة نموذج عالمي عند 250 مترًا، لا تحليل تربة.",
            "ترتيب الحقول ترتيب، لا درجة صحّة معايَرة.",
            "لا يُسمّى أي مرض من صور الأقمار. فالمرض ونقص الماء ونقص "
            "النيتروجين والملوحة وضرر الآفات والرقاد تحرّك هذه النطاقات معًا "
            "ولا تفصلها. والشذوذ يقول إنّ بقعة تختلف عن بقيّة الحقل، ولا "
            "يسمّي سببًا.",
            "خطر الإصابة مواتاة طقس من نماذج منشورة غير مُتحقَّق منها في "
            "السودان، وهو يصف الهواء فوق المنطقة — ويصدق بالقدر نفسه على كل "
            "حقل سليم تحت تلك السماء.",
            "معاملات المحاصيل من FAO-56 وأرقام منشورة تقليدية، لا من تجارب "
            "سودانية. وعتبة الحرارة صفة صنف بقدر ما هي صفة نوع.",
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
        # Already analysed on an earlier attempt at the SAME question - the
        # fingerprint has been checked, so this is the field's own result and
        # not a stale one from a different run.
        if name in already:
            results["fields"].append(already[name])
            print(f"\n[{i}/{len(feats)}] {name}  (from the checkpoint)")
            continue
        geom = ee.Geometry(f["geometry"])
        print(f"\n[{i}/{len(feats)}] {name}")

        ref, ref_prov = neighbourhood_for(f, geom)
        print(f"  reference        : {ref_prov['reference_source'].split(':')[0]}")

        health = crop_health(geom, ref, start, end)
        vig = health["readings"].get("vigour", {})
        print(f"  vigour           : {vig.get('status')}"
              + (f"  {vig.get('value')}" if vig.get("value") is not None else ""))

        # THE CROP IS THE FIELD'S, NOT THE RUN'S. A tenancy rotates cotton,
        # sorghum, wheat and groundnut, and giving a wheat block sorghum's heat
        # threshold produces a wrong number rather than a missing one.
        fc = field_crop(f, crop)
        if fc["source"] == "field":
            print(f"  crop             : {fc['key']} (declared on the field)")
        if not fc["recognised"]:
            print(f"  crop             : UNRECOGNISED \"{fc['declared']}\" - "
                  "generic parameters used")

        rec = {
            "name": name,
            "properties": f.get("properties", {}),
            "crop": fc,
            "reference_provenance": ref_prov,
            "crop_health": health,
            "thermal_stress": thermal_stress(geom, ref, start, end,
                                             f.get("geometry")),
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
            rec["yield_estimate"] = agro.yield_estimate(v, fc["key"])
            if days and values:
                rec["phenology"] = phenology(days, values)
            # Does the observed canopy imply a coefficient anywhere near the
            # published range for the crop this field claims to be growing? A
            # check on the LABEL, not on the water figure, which is derived
            # from greenness on purpose.
            wr = rec.get("water_requirement") or {}
            if wr.get("kcb") is not None:
                rec["crop_check"] = cr.kcb_plausible(wr["kcb"], fc["key"])

        if ncg is not None:
            try:
                rec["climate"] = ncg.climate_context(geom, start, end, fc["key"])
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
                    nres = ncg.nutrition_status(fvals, scheme_stats, fc["key"],
                                                reference_indices=strip_vals)
                    rec["nutrition"] = ncg.asdict_nutrition(nres)
                else:
                    rec["nutrition"] = {"status": "NOT AVAILABLE",
                                        "reason": "too few Sentinel-2 scenes"}
            except Exception as e:
                rec["nutrition"] = {"status": "NOT AVAILABLE", "reason": str(e)[:140]}

        # Rung 1 then rungs 2 and 3. The anomaly scan is computed first because
        # the ladder needs it: a patch in THIS field outranks a weather window
        # over every field.
        anomaly = anomaly_scan(geom, f.get("geometry"), start, end)
        rec["anomaly"] = anomaly
        if anomaly.get("flagged"):
            print(f"  anomaly          : {anomaly['area_ha']} ha in the "
                  f"{anomaly.get('where')} - cause unknown")
        # Rung 3 comes from what a person recorded. The observation store is
        # preferred over the field file because it is where the app writes; a
        # `scouting` list on the feature is still honoured, for a field file
        # prepared by hand or by another system.
        scouting = list((f.get("properties") or {}).get("scouting") or [])
        if obs_store is not None:
            scouting += obs_store.scouting_for(name)
        rec["disease"] = disease_layer(geom, start, end, fc["key"],
                                       anomaly=anomaly, scouting=scouting,
                                       agro=agro, weather=farm_weather,
                                       rain=farm_rain)
        n_fav = rec["disease"].get("risk", {}).get("n_favourable", 0)
        if n_fav:
            print(f"  weather windows  : {n_fav} favourable to infection "
                  "(about the air, not this field)")

        if fr is not None:
            rec["advisory"] = fr.advisory(rec, canal_record=None, lang="ar")
            rec["advisory_en"] = fr.advisory(rec, canal_record=None, lang="en")

        results["fields"].append(rec)
        check.add(rec)

    results["ranking"] = rank_fields(results["fields"])
    results["checkpoint"] = check.describe()
    # What is actually standing on this farm, and where each label came from.
    # A run that silently applied one crop to everything looked identical to a
    # run over a genuinely single-crop farm.
    present = {}
    for r in results["fields"]:
        k = (r.get("crop") or {}).get("key", "default")
        present[k] = present.get(k, 0) + 1
    results["crops_present"] = present
    results["crop_source"] = (
        "each field's own `crop` property where it has one, otherwise the "
        "crop given to the run")
    results["coarse_sampling"] = {"ERA5-Land": era5_fit, "CHIRPS": chirps_fit}
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
    # Only now. A half-finished report sitting at the report's own path would
    # be read AS a report - by a person, by the run store, by the change page -
    # and a farm whose worst fields happened to come last would look fine.
    check.done()
    print(f"\nWritten to {out_json}")
    return results
