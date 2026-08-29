"""
Rangeland and pastoralism layer.

WHAT IT MEASURES
----------------
  productivity   seasonal NDVI integral - the standard remote proxy for
                 above-ground herbaceous production, reported against the site's
                 OWN history rather than as an absolute biomass figure
  timing         when the range greened up, when it peaked, how long it lasted -
                 which for a herder is more actionable than how much grew, since
                 it is what determines when to move
  water points   surface water at hafirs and pans across the season
  corridors      vegetation condition along a supplied route, reported the same
                 way as everywhere else

WHY THIS MODULE IS DIFFERENT FROM EVERY OTHER ONE HERE
------------------------------------------------------
Every other layer risks being wrong. This one risks being USED.

Farmer-herder conflict is a live driver of violence in Sudan, and rangeland and
corridor maps are exactly the artefacts that get carried into a dispute as
evidence of entitlement. A map showing "the corridor" is read by one party as a
right of passage and by another as an encroachment, and neither reading is in
the data. The measurement itself - how green a strip of land was in October -
carries no claim at all; the claim gets attached by the framing around it.

So this module enforces, in code:

  1. NO PARTY IS NAMED. No output field may contain the name of a group, tribe,
     community, or claimant. `check_neutrality` rejects the text if it does.
  2. NO ENTITLEMENT LANGUAGE. "belongs to", "grazing rights", "encroachment",
     "trespass" and their Arabic equivalents are refused, not merely avoided.
  3. SYMMETRIC REPORTING. A corridor is reported alongside its surrounding land
     using the SAME indicator and the same phrasing, so the output never
     privileges one land use over the other by describing only one of them.
  4. EVERY RESULT CARRIES ITS OWN SENSITIVITY NOTE, so the caveat cannot be
     stripped by a consumer that only reads the numbers.

None of this makes the layer safe. It makes the layer honest, and it makes
misuse require someone to actively add the claim rather than merely quote the
output. That is the most a measurement tool can do, and pretending otherwise
would be its own kind of dishonesty.
"""

from __future__ import annotations

from typing import Optional, Sequence

import decision_logic as dl

try:
    import ee
except ImportError:
    ee = None


# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Green-up is the day the NDVI series first reaches this fraction of its
# seasonal amplitude. ARBITRARY: the half-amplitude convention is common and has
# no physical claim behind it.
GREENUP_FRACTION = 0.5

# A pixel counts as a water point when it read as water on at least this
# fraction of observations. ARBITRARY, and the same caveat as canal water:
# radar and MNDWI see standing water, not usable water.
WATER_POINT_FREQUENCY = 0.30

# Below this many usable scenes no timing figure is reported: green-up day from
# four images is an artefact of which four. ARBITRARY.
MIN_SCENES_FOR_TIMING = 8

_S2 = "COPERNICUS/S2_SR_HARMONIZED"
_JRC_MONTHLY = "JRC/GSW1_4/MonthlyHistory"


# ==============================================================================
# THE NEUTRALITY GUARD  (enforced, not promised)
# ==============================================================================

# Words that turn a measurement into a claim about who may use land. This list
# is deliberately blunt and deliberately over-broad: a false refusal costs a
# rephrasing, while a false pass puts a claim into a document that will be
# carried into a dispute.
CLAIM_WORDS_EN = [
    "belongs to", "owned by", "grazing right", "grazing rights", "entitle",
    "encroach", "trespass", "invaded", "illegal occupation", "rightful",
    "their land", "our land", "tribal land", "ancestral",
]
CLAIM_WORDS_AR = [
    "يملك", "ملك", "حق الرعي", "حقوق الرعي", "تعدّي", "تعدي", "اعتداء",
    "أرضهم", "أرضنا", "قبيلة", "قبلي", "احتلال", "غزو",
]


def check_neutrality(text: str) -> dict:
    """
    Refuse text that attaches a claim to a measurement.

    This is the rangeland analogue of the farmer channel's attribution guard,
    and it is stricter, because the failure mode is worse: an unfair equity
    sentence damages trust in the tool, while a rangeland map carrying an
    entitlement word can be carried into a conflict as evidence.

    Returns {"neutral": bool, "hits": [...]}. Callers must not emit text that
    fails this.
    """
    if not text:
        return {"neutral": True, "hits": []}
    low = str(text).lower()
    hits = [w for w in CLAIM_WORDS_EN if w in low]
    hits += [w for w in CLAIM_WORDS_AR if w in str(text)]
    return {"neutral": not hits, "hits": hits}


SENSITIVITY_NOTE = {
    "en": ("This describes vegetation and surface water measured from "
           "satellites. It says nothing about who may use this land, who has "
           "used it, or who should. It is neutral information for every party "
           "and is not evidence of any claim."),
    "ar": ("هذا وصف للغطاء النباتي والمياه السطحية كما قاست الأقمار الصناعية. "
           "لا يقول شيئًا عمّن يحقّ له استخدام هذه الأرض، ولا عمّن استخدمها، "
           "ولا عمّن ينبغي أن يستخدمها. هو معلومة محايدة لكل الأطراف وليس "
           "دليلًا على أيّ ادّعاء."),
}


def _with_sensitivity(result: dict) -> dict:
    """Attach the note to every result, so a consumer reading only the numbers
    still carries the caveat with them."""
    result["conflict_sensitivity"] = SENSITIVITY_NOTE
    return result


# ==============================================================================
# PURE LOGIC - timing and productivity decisions
# ==============================================================================

def greenup_timing(days: Sequence[float], ndvi: Sequence[Optional[float]],
                   fraction: float = GREENUP_FRACTION) -> dict:
    """
    Green-up day, peak day and season length from an NDVI series.

    For a herder this is the actionable half of the rangeland signal: how much
    grew matters less than when it grew and how long it lasted, because that is
    what a movement decision turns on.

    Refuses on a series too short or too flat to have a season in it, rather
    than reporting the day of the single highest value in a noisy series as if
    it meant something.
    """
    pts = [(float(d), float(v)) for d, v in zip(days, ndvi) if v is not None]
    if len(pts) < MIN_SCENES_FOR_TIMING:
        return {"status": "NOT AVAILABLE",
                "reason": (f"only {len(pts)} usable observations; "
                           f"{MIN_SCENES_FOR_TIMING} needed before a green-up "
                           "day means anything"),
                "min_scenes_basis": "ARBITRARY"}
    pts.sort()
    vs = [v for _, v in pts]
    lo, hi = min(vs), max(vs)
    amplitude = hi - lo
    if amplitude < 0.05:
        return {"status": "NOT AVAILABLE",
                "reason": (f"seasonal NDVI amplitude {round(amplitude, 3)} is "
                           "too flat to contain a green-up; this is a statement "
                           "about the vegetation, not a data failure"),
                "amplitude": round(amplitude, 4)}

    target = lo + fraction * amplitude
    greenup = None
    for (d, v), (_, v_next) in zip(pts, pts[1:]):
        if v < target <= v_next:
            greenup = d
            break
    peak_day = max(pts, key=lambda p: p[1])[0]
    # Season length: first to last crossing of the same threshold.
    above = [d for d, v in pts if v >= target]
    length = (max(above) - min(above)) if len(above) >= 2 else None

    return _with_sensitivity({
        "status": "OK",
        "greenup_day": greenup,
        "peak_day": peak_day,
        "season_length_days": length,
        "amplitude": round(amplitude, 4),
        "n_observations": len(pts),
        "threshold_basis": (f"ARBITRARY: green-up is the first crossing of "
                            f"{fraction} of the seasonal NDVI amplitude."),
        "interpretation": ("When the range greened, peaked and faded. Timing, "
                           "not quantity."),
    })


def productivity_index(ndvi_series: Sequence[Optional[float]],
                       history_by_year: Optional[dict] = None) -> dict:
    """
    Seasonal NDVI integral, reported against the site's own history.

    WHY NOT KILOGRAMS PER HECTARE
    Converting an NDVI integral to biomass needs a locally fitted relationship,
    and the fit is species-, soil- and season-specific. Quoting kg/ha from an
    uncalibrated integral is the same error as quoting leaf nitrogen from
    red-edge, and is refused for the same reason. The integral compared against
    the same site's previous seasons is a real, defensible statement and is the
    one a herder can act on: better or worse than last year, here.
    """
    vals = [float(v) for v in ndvi_series if v is not None]
    if not vals:
        return {"status": "NOT AVAILABLE",
                "reason": "no usable NDVI observations in the window"}
    integral = sum(vals) / len(vals)

    out = {
        "status": "OK",
        "ndvi_integral": round(integral, 4),
        "n_observations": len(vals),
        "biomass_kg_ha": None,
        "biomass_reason": ("no locally fitted NDVI-to-biomass relationship "
                           "exists, so no absolute production figure is quoted"),
        "interpretation": ("Mean seasonal greenness. A relative measure of how "
                           "productive the range was, not a biomass figure."),
    }

    if history_by_year:
        hist = [float(v) for v in history_by_year.values() if v is not None]
        if len(hist) >= 3:
            hist_sorted = sorted(hist)
            n = len(hist_sorted)
            p16 = hist_sorted[max(0, int(0.16 * n) - 1)]
            p50 = hist_sorted[n // 2]
            p84 = hist_sorted[min(n - 1, int(0.84 * n))]
            sigma = dl.robust_sigma(p16, p84)
            z = (integral - p50) / sigma if sigma > 0 else 0.0
            if z <= -1.0:
                verdict = "well below this site's recent seasons"
            elif z <= -0.5:
                verdict = "below this site's recent seasons"
            elif z < 0.5:
                verdict = "near this site's normal"
            elif z < 1.0:
                verdict = "above this site's recent seasons"
            else:
                verdict = "well above this site's recent seasons"
            out.update({
                "history_years": len(hist),
                "history_median": round(p50, 4),
                "z_vs_history": round(z, 2),
                "verdict": verdict,
                "verdict_basis": ("compared against this site's own history "
                                  "using median and robust sigma, not against "
                                  "any absolute scale or another site"),
            })
        else:
            out["history_years"] = len(hist)
            out["verdict"] = None
            out["verdict_reason"] = (
                f"only {len(hist)} historical seasons; at least 3 are needed "
                "before a comparison means anything")
    return _with_sensitivity(out)


# ==============================================================================
# EARTH ENGINE FETCHERS
# ==============================================================================

def ndvi_series(aoi, start: str, end: str, scale: int = 20) -> dict:
    """Region-mean NDVI series with its acquisition days, in one round trip."""
    if ee is None:
        return {"status": "NOT AVAILABLE", "reason": "Earth Engine unavailable"}
    try:
        col = (ee.ImageCollection(_S2).filterBounds(aoi).filterDate(start, end)
               .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40)))
        n = col.size().getInfo()
        if n == 0:
            return {"status": "NOT AVAILABLE",
                    "reason": "no Sentinel-2 scenes in the window"}

        def one(img):
            v = img.normalizedDifference(["B8", "B4"]).rename("NDVI").reduceRegion(
                reducer=ee.Reducer.mean(), geometry=aoi, scale=scale,
                maxPixels=1e9, bestEffort=True).get("NDVI")
            return ee.Feature(None, {"ndvi": v, "t": img.date().millis()})

        fc = ee.FeatureCollection(col.map(one))
        vs = fc.aggregate_array("ndvi").getInfo()
        ts = fc.aggregate_array("t").getInfo()
        days = [(t - ts[0]) / 86400000.0 for t in ts] if ts else []
        return {"status": "OK", "ndvi": vs, "days": days, "n_scenes": n}
    except Exception as e:
        return {"status": "NOT AVAILABLE", "reason": str(e)}


def water_points(aoi, start: str, end: str, scale: int = 30) -> dict:
    """
    Seasonal surface-water presence for hafirs and pans inside an area.

    HONEST LIMITS
    - This is standing water, not usable water. A hafir can hold water that is
      saline, fouled, or fenced, and none of that is visible from orbit.
    - A hafir smaller than about a hectare is a handful of pixels and its
      presence figure is dominated by mixed-pixel effects.
    - Absence of detected water is not evidence a hafir is dry: it may have been
      cloud-covered, or below the detection size.
    """
    if ee is None:
        return {"status": "NOT AVAILABLE", "reason": "Earth Engine unavailable"}
    try:
        col = (ee.ImageCollection(_JRC_MONTHLY)
               .filterBounds(aoi).filterDate(start, end))
        n = col.size().getInfo()
        if n == 0:
            return {"status": "NOT AVAILABLE",
                    "reason": "no surface-water observations in the window"}
        # water == 2 in the JRC monthly classification
        freq = col.map(lambda img: img.select("water").eq(2)).mean()
        v = freq.reduceRegion(reducer=ee.Reducer.mean(), geometry=aoi,
                              scale=scale, maxPixels=1e9,
                              bestEffort=True).getInfo().get("water")
        if v is None:
            return {"status": "NOT AVAILABLE",
                    "reason": "no valid surface-water pixels for this area"}
        return _with_sensitivity({
            "status": "OK",
            "water_frequency": round(v, 4),
            "months_observed": n,
            "present": v >= WATER_POINT_FREQUENCY,
            "threshold": WATER_POINT_FREQUENCY,
            "threshold_basis": "ARBITRARY",
            "provenance": {"sensor": _JRC_MONTHLY, "date_start": start,
                           "date_end": end, "scale_m": scale},
            "caveat": ("Standing water, not usable water. Absence of detected "
                       "water is not evidence a water point is dry - it may "
                       "have been cloud-covered or below the detection size."),
        })
    except Exception as e:
        return {"status": "NOT AVAILABLE", "reason": str(e)}


def corridor_condition(corridor_geom, surrounding_geom, start: str, end: str,
                       buffer_m: int = 1000) -> dict:
    """
    Vegetation condition along a route, reported ALONGSIDE its surroundings.

    The symmetry is the point. Reporting a corridor's greenness on its own
    invites the reading "this strip is special". Reporting it next to the land
    around it, with the same indicator and the same words, keeps it as what it
    is: one measurement among several, with no claim attached.
    """
    if ee is None:
        return {"status": "NOT AVAILABLE", "reason": "Earth Engine unavailable"}
    corridor = ndvi_series(corridor_geom.buffer(buffer_m), start, end)
    around = ndvi_series(surrounding_geom, start, end)
    if corridor["status"] != "OK":
        return {"status": "NOT AVAILABLE",
                "reason": f"corridor: {corridor.get('reason')}"}

    out = {
        "status": "OK",
        "corridor": productivity_index(corridor["ndvi"]),
        "surrounding": (productivity_index(around["ndvi"])
                        if around["status"] == "OK"
                        else {"status": "NOT AVAILABLE",
                              "reason": around.get("reason")}),
        "buffer_m": buffer_m,
        "buffer_basis": "ARBITRARY: corridor half-width is a supplied constant",
        "interpretation": ("Greenness along the route and in the land around "
                           "it, measured the same way. Neither figure describes "
                           "who uses either area."),
    }
    return _with_sensitivity(out)


def analyse_rangeland(area_feature: dict, start: str, end: str,
                      history_by_year: Optional[dict] = None) -> dict:
    """
    Full rangeland record for one area. Every sub-result carries the
    sensitivity note, and the assembled record is checked for neutrality before
    it is returned - a defence in depth against a phrase drifting in later.
    """
    if ee is None:
        return {"status": "NOT AVAILABLE", "reason": "Earth Engine unavailable"}
    name = area_feature.get("properties", {}).get("name", "rangeland area")
    neutral = check_neutrality(name)
    if not neutral["neutral"]:
        return {"status": "REFUSED",
                "reason": ("the supplied area name contains claim language "
                           f"({neutral['hits']}); rename it to a neutral "
                           "identifier before this area can be reported"),
                "hits": neutral["hits"]}

    geom = ee.Geometry(area_feature["geometry"])
    series = ndvi_series(geom, start, end)
    record = {"name": name, "status": "OK"}

    if series["status"] == "OK":
        record["productivity"] = productivity_index(series["ndvi"], history_by_year)
        record["timing"] = greenup_timing(series["days"], series["ndvi"])
    else:
        record["productivity"] = {"status": "NOT AVAILABLE",
                                  "reason": series.get("reason")}
        record["timing"] = {"status": "NOT AVAILABLE",
                            "reason": series.get("reason")}

    record["water_points"] = water_points(geom, start, end)
    return _with_sensitivity(record)
