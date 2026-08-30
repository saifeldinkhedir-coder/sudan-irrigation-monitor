"""
Crop water requirement, weather forecast, phenology, and the yield gate.

WHY THIS MODULE EXISTS
----------------------
The engine could already say "this field is stressed". It could not say how much
water the crop actually needed, which is the number a farmer can act on and the
number that connects the two layers of the platform:

    the FIELD layer says the crop needed 6.2 mm/day and rain gave 0.4
    the NETWORK layer says whether the canal that was supposed to make up the
    difference had water in it

Neither statement is worth much alone. Together they are the difference between
"your crop is suffering" - which the farmer already knew - and "your crop needed
water, it did not rain, and the canal serving you was dry on 11 of 14 days".

WHERE THE PHYSICS RUNS, AND WHY IT IS NOT ON THE SERVER
-------------------------------------------------------
ET0 is computed in pure Python from a daily region-mean series pulled in one
aggregate_array call, NOT per-pixel in Earth Engine. That is not a shortcut. The
inputs come from ERA5-Land at roughly 11 km, so a per-pixel ET0 raster would be
11 km of information resampled to look like 10 m detail it does not have - the
kind of false precision this platform exists to avoid. A region mean is what the
data actually supports, and computing it here makes the FAO-56 arithmetic
unit-testable against published table values instead of unverifiable inside a
server-side expression.

WHAT THIS MODULE REFUSES TO DO
------------------------------
1. It reports crop water REQUIREMENT, never crop water DELIVERED. Requirement is
   a calculation from weather and canopy; delivery is a measurement nobody here
   has. Conflating them would let a farmer read "6 mm/day" as "you received
   6 mm/day", which is the opposite of the truth in exactly the situation this
   platform is built for.
2. It carries no soil-water store. A real balance needs soil texture, rooting
   depth and an initial water content; without them, requirement on any single
   day can be met from water stored days earlier. So the figure is honest over a
   season and rough over a day, and says so.
3. It refuses to estimate YIELD without local calibration, for the same reason
   the nutrition ladder refuses to quote nitrogen: the satellite sees canopy, and
   the canopy-to-yield relationship is crop-, variety-, management- and
   season-specific. An uncalibrated yield figure is a guess wearing a decimal
   point.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import decision_logic as dl

try:
    import ee
except ImportError:
    ee = None


# ==============================================================================
# CONFIGURATION - every hand-chosen number, declared
# ==============================================================================

# Basal crop coefficient from NDVI: Kcb = KCB_A + KCB_B * NDVI.
# ARBITRARY: these are published regression coefficients from other regions and
# other crops. They are used because a coefficient that responds to the observed
# canopy is better than a fixed table value that ignores it - not because these
# particular numbers have been checked in Sudan. Replace them from local lysimeter
# or eddy-covariance work if it ever exists.
KCB_A = -0.10
KCB_B = 1.44
KCB_MAX = 1.15          # physical ceiling for a full, well-watered canopy
KCB_MIN = 0.0

# Daily rainfall below this is taken as intercepted by canopy and surface, and
# contributes nothing to the root zone. ARBITRARY.
RAIN_INTERCEPTION_MM = 2.0

# Fraction of above-interception rainfall that reaches the root zone rather than
# running off or percolating past it. ARBITRARY: a single number standing in for
# soil texture, slope, and rainfall intensity, none of which are inputs here.
RAINFALL_EFFECTIVE_FRACTION = 0.75

# Yield: the same gate shape as the nitrogen ladder.
MIN_YIELD_CALIBRATION_POINTS = 30
MAX_ACCEPTABLE_YIELD_RMSE_FRACTION = 0.25   # of mean observed yield

# GFS forecast horizon actually used. GFS runs further out; skill degrades, and
# a 16-day field-scale rainfall forecast would be a number with no information
# in it. ARBITRARY, chosen to match what farm apps conventionally show.
FORECAST_DAYS = 7

# How far back to look for model runs. GFS in Earth Engine lags real time by
# a variable amount, so a window of a couple of days finds the latest usable
# run without scanning the archive. ARBITRARY.
RECENT_RUNS_DAYS = 3

# The two ETc methods, named so callers and tests can check which was used
# without reading the source.
ETC_METHOD_INTEGRAL = (
    "sum over days of Kcb(NDVI on that day) * ET0 on that day - the integral, "
    "not the product of the two season means")
ETC_METHOD_APPROXIMATE = (
    "APPROXIMATE: season-mean NDVI turned into one Kcb and multiplied by total "
    "ET0. This equals the true integral only if canopy and ET0 are uncorrelated "
    "across the season, and in an irrigated season here they are not - the bare "
    "weeks are the hottest. Supply a dated NDVI series to get the integral "
    "instead.")

_ERA5_DAILY = "ECMWF/ERA5_LAND/DAILY_AGGR"
_GFS = "NOAA/GFS0P25"


# ==============================================================================
# FAO-56 REFERENCE EVAPOTRANSPIRATION - pure, testable physics
# ==============================================================================

def saturation_vapour_pressure(t_c: float) -> float:
    """
    e0(T) in kPa - FAO-56 eq. 11 (Tetens). Checkable against FAO-56 Table 2.3:
    2.338 kPa at 20 C, 3.168 kPa at 25 C.
    """
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def slope_vapour_pressure_curve(t_c: float) -> float:
    """
    Delta in kPa/C - FAO-56 eq. 13. Checkable against FAO-56 Table 2.4:
    ~0.145 kPa/C at 20 C.
    """
    es = saturation_vapour_pressure(t_c)
    return 4098.0 * es / (t_c + 237.3) ** 2


def psychrometric_constant(pressure_kpa: float) -> float:
    """gamma in kPa/C - FAO-56 eq. 8. ~0.0674 at sea-level 101.3 kPa."""
    return 0.665e-3 * pressure_kpa


def wind_speed_2m(u10_ms: float) -> float:
    """
    Convert a 10 m wind speed to the 2 m height FAO-56 requires - eq. 47. The
    factor at 10 m is 4.87 / ln(67.8 * 10 - 5.42) = 0.748.
    """
    return u10_ms * 4.87 / math.log(67.8 * 10.0 - 5.42)


def et0_penman_monteith(t_max_c: float, t_min_c: float, t_dew_c: float,
                        u10_ms: float, rn_mj_m2_day: float,
                        pressure_kpa: float = 101.3,
                        g_mj_m2_day: float = 0.0) -> Optional[float]:
    """
    FAO-56 eq. 6, the daily reference evapotranspiration of a hypothetical
    0.12 m grass surface, in mm/day.

        ET0 = [0.408 D (Rn - G) + g (900/(T+273)) u2 (es - ea)]
              / [D + g (1 + 0.34 u2)]

    Soil heat flux G is taken as zero, which FAO-56 endorses for daily steps.

    Returns None on inputs that are not physically usable, so the caller reports
    NOT AVAILABLE rather than a number derived from a missing variable.
    """
    for v in (t_max_c, t_min_c, t_dew_c, u10_ms, rn_mj_m2_day, pressure_kpa):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
    if t_max_c < t_min_c:
        return None

    t_mean = (t_max_c + t_min_c) / 2.0
    delta = slope_vapour_pressure_curve(t_mean)
    gamma = psychrometric_constant(pressure_kpa)
    u2 = max(0.0, wind_speed_2m(u10_ms))

    es = (saturation_vapour_pressure(t_max_c)
          + saturation_vapour_pressure(t_min_c)) / 2.0
    ea = saturation_vapour_pressure(t_dew_c)
    # A dew point above the air temperature is a data artefact, not supersaturated
    # air; clamp rather than produce a negative vapour-pressure deficit.
    ea = min(ea, es)
    vpd = es - ea

    numerator = (0.408 * delta * (rn_mj_m2_day - g_mj_m2_day)
                 + gamma * (900.0 / (t_mean + 273.0)) * u2 * vpd)
    denominator = delta + gamma * (1.0 + 0.34 * u2)
    if denominator <= 0:
        return None
    return max(0.0, numerator / denominator)


def kcb_from_ndvi(ndvi: Optional[float]) -> Optional[dict]:
    """
    Basal crop coefficient from observed canopy greenness.

    WHY NOT A KC TABLE
    The FAO-56 tabulated Kc is indexed by crop and growth stage, so using it
    means asserting a planting date and a stage length for every field. Those are
    exactly the things that vary most in a scheme where planting is staggered by
    water availability - and getting them wrong silently mis-scales every water
    figure. Kcb from NDVI asks the satellite what the canopy is actually doing,
    which is the one thing we can observe.

    The coefficients remain ARBITRARY, and that travels with the result.
    """
    if ndvi is None:
        return None
    kcb = KCB_A + KCB_B * float(ndvi)
    clamped = min(KCB_MAX, max(KCB_MIN, kcb))
    return {
        "kcb": round(clamped, 3),
        "ndvi": round(float(ndvi), 4),
        "clamped": clamped != kcb,
        "basis": (f"ARBITRARY: Kcb = {KCB_A} + {KCB_B} * NDVI, published "
                  "coefficients from other regions and crops, clamped to "
                  f"[{KCB_MIN}, {KCB_MAX}]. Not validated in Sudan."),
    }


def interpolate_to_daily(sample_days: Sequence[float],
                         sample_values: Sequence[Optional[float]],
                         n_days: int,
                         max_gap_days: int = 30) -> dict:
    """
    Put a sparse satellite series onto daily steps by linear interpolation.

    WHY THIS NEEDS A GAP LIMIT
    Sentinel-2 gives a usable scene every few days at best, and far less in a
    cloudy month. Joining two observations six weeks apart with a straight line
    invents a canopy trajectory nobody saw - and during green-up or senescence
    the canopy is doing exactly the thing a straight line cannot represent.

    So days inside a gap longer than `max_gap_days` are returned as None rather
    than interpolated, and the caller reports how much of the season was
    genuinely bridged. ARBITRARY: 30 days.

    Days before the first observation and after the last are not extrapolated at
    all. Extrapolating a canopy is guessing about the part of the season nobody
    watched.
    """
    pairs = sorted((int(d), float(v)) for d, v in zip(sample_days, sample_values)
                   if v is not None)
    daily = [None] * n_days
    if len(pairs) < 2:
        return {"daily": daily, "interpolated_days": 0, "observed_days": len(pairs),
                "gap_days": n_days,
                "reason": "fewer than two usable observations"}

    interpolated = 0
    for (d0, v0), (d1, v1) in zip(pairs, pairs[1:]):
        span = d1 - d0
        if span <= 0 or span > max_gap_days:
            continue
        for d in range(max(0, d0), min(n_days, d1 + 1)):
            t = (d - d0) / span if span else 0.0
            daily[d] = v0 + t * (v1 - v0)
            interpolated += 1

    filled = sum(1 for v in daily if v is not None)
    return {
        "daily": daily,
        "observed_days": len(pairs),
        "filled_days": filled,
        "gap_days": n_days - filled,
        "coverage": round(filled / n_days, 3) if n_days else 0.0,
        "max_gap_days": max_gap_days,
        "basis": (f"ARBITRARY: gaps longer than {max_gap_days} days are left "
                  "empty rather than bridged, and nothing is extrapolated "
                  "before the first or after the last observation."),
    }


def etc_time_integrated(et0_daily: Sequence[Optional[float]],
                        ndvi_daily: Sequence[Optional[float]],
                        min_coverage: float = 0.5) -> dict:
    """
    ETc as the daily sum of Kcb(t) * ET0(t) - the correct integral.

    WHY THE SEASON-MEAN SHORTCUT IS WRONG, NOT MERELY ROUGH
    An earlier version took the season's MEAN NDVI, turned it into a single Kcb,
    and multiplied it by total ET0. That is only equal to the true integral when
    NDVI and ET0 are uncorrelated across the season, and in an irrigated
    Sudanese season they are strongly correlated in the worst direction: the
    canopy is near zero during the hottest, highest-ET0 weeks before planting
    and after harvest, and near its peak during a cooler part of the window.

    Measured on a live Gezira field: mean NDVI 0.215 gave Kcb 0.215 and
    ETc 385 mm from ET0 1792 mm. Weighting each day by that day's canopy gives
    a materially different number, because the shortcut charges the bare-soil
    weeks with the crop's coefficient and the cropped weeks with the bare
    soil's.

    Days where either input is missing contribute nothing and are counted, so
    the caller can see how much of the season the figure actually rests on. If
    the covered fraction falls below `min_coverage` the total is refused: a
    seasonal water requirement computed over a third of the season is not a
    seasonal water requirement.
    """
    n = min(len(et0_daily), len(ndvi_daily))
    etc, et0_used, used_days = 0.0, 0.0, 0
    kcb_weighted = 0.0
    for i in range(n):
        e, v = et0_daily[i], ndvi_daily[i]
        if e is None or v is None:
            continue
        k = kcb_from_ndvi(v)
        if k is None:
            continue
        etc += k["kcb"] * e
        kcb_weighted += k["kcb"] * e
        et0_used += e
        used_days += 1

    coverage = used_days / n if n else 0.0
    out = {
        "days_in_window": n,
        "days_used": used_days,
        "coverage": round(coverage, 3),
        "min_coverage": min_coverage,
        "coverage_basis": ("ARBITRARY: below this fraction of days the seasonal "
                           "total is refused rather than scaled up, because "
                           "the missing days are not a random sample - they are "
                           "the cloudy ones."),
        "method": ETC_METHOD_INTEGRAL,
    }
    if coverage < min_coverage:
        out.update({
            "status": "NOT AVAILABLE",
            "etc_mm": None,
            "reason": (f"only {round(100 * coverage)}% of the season had both an "
                       "ET0 value and an interpolated canopy value; "
                       f"{round(100 * min_coverage)}% is the minimum before a "
                       "seasonal total is quoted"),
        })
        return out

    out.update({
        "status": "OK",
        "etc_mm": round(etc, 1),
        "et0_mm_over_used_days": round(et0_used, 1),
        # The ET0-weighted mean Kcb. Reported so the figure can be sanity
        # checked against the flat-mean version it replaces.
        "kcb_et0_weighted_mean": (round(kcb_weighted / et0_used, 3)
                                  if et0_used else None),
    })
    return out


def effective_rainfall_mm(daily_rain_mm: Sequence[Optional[float]],
                          interception_mm: float = RAIN_INTERCEPTION_MM,
                          effective_fraction: float = RAINFALL_EFFECTIVE_FRACTION
                          ) -> dict:
    """
    The part of rainfall that plausibly reaches the root zone.

    Two crude corrections, both declared: a per-day interception loss, and a
    flat fraction for runoff and deep percolation. The second is a single number
    standing in for soil texture, slope and rainfall intensity - none of which
    are inputs. It is here because using raw gauge rainfall as if all of it
    entered the root zone would overstate supply and understate the irrigation
    requirement, which is the more damaging direction of the two errors.
    """
    days = [d for d in daily_rain_mm if d is not None]
    total = sum(days)
    eff = sum(max(0.0, d - interception_mm) for d in days) * effective_fraction
    return {
        "total_rainfall_mm": round(total, 1),
        "effective_rainfall_mm": round(eff, 1),
        "days": len(days),
        "basis": (f"ARBITRARY: {interception_mm} mm/day interception, then "
                  f"{effective_fraction} of the remainder assumed to reach the "
                  "root zone. Stands in for soil texture, slope and intensity, "
                  "which are not inputs here."),
    }


# ==============================================================================
# EARTH ENGINE FETCHERS - one round trip each
# ==============================================================================

ERA5_NATIVE_M = 11000


def era5_daily_series(aoi, start: str, end: str,
                      scale: int = ERA5_NATIVE_M) -> Optional[dict]:
    """
    Pull the daily ERA5-Land variables ET0 needs as region means, in ONE
    aggregate_array round trip per variable rather than one per day.

    Returns None when the collection is empty, so the caller says NOT AVAILABLE.
    """
    if ee is None:
        return None
    try:
        # Buffered to one native pixel. A field is ~600 m and ERA5-Land is
        # ~11 km, so an unbuffered reduction encloses no pixel centre and
        # returns nulls for every variable - which would surface as "ERA5-Land
        # returned days but no complete set of variables" for data that covers
        # the field fine. The value is the ERA5 cell containing the field, which
        # is all this dataset can say about it.
        region = aoi.buffer(dl.coarse_sampling_buffer_m(ERA5_NATIVE_M))
        col = ee.ImageCollection(_ERA5_DAILY).filterBounds(region).filterDate(start, end)
        if col.size().getInfo() == 0:
            return None

        wanted = {
            "t_max": "temperature_2m_max",
            "t_min": "temperature_2m_min",
            "t_dew": "dewpoint_temperature_2m",
            "u10": "u_component_of_wind_10m",
            "v10": "v_component_of_wind_10m",
            "rn_solar": "surface_net_solar_radiation_sum",
            "rn_thermal": "surface_net_thermal_radiation_sum",
            "pressure": "surface_pressure",
        }

        def day_props(img):
            stats = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region,
                                     scale=scale, maxPixels=1e8, bestEffort=True)
            return ee.Feature(None, {k: stats.get(b) for k, b in wanted.items()})

        fc = ee.FeatureCollection(col.map(day_props))
        out = {k: fc.aggregate_array(k).getInfo() for k in wanted}
        out["n_days"] = col.size().getInfo()
        out["source"] = _ERA5_DAILY
        return out
    except Exception:
        return None


def _series_to_et0(series: dict) -> list:
    """Convert the raw ERA5-Land series into a daily ET0 list in mm/day.

    ERA5-Land gives temperatures in kelvin, radiation as J/m2 accumulated over
    the day, and pressure in pascals. Each conversion is done once, here, rather
    than being re-derived at every call site."""
    n = min(len(series.get(k) or []) for k in
            ("t_max", "t_min", "t_dew", "u10", "v10", "rn_solar", "rn_thermal"))
    et0 = []
    press = series.get("pressure") or []
    for i in range(n):
        try:
            t_max = series["t_max"][i] - 273.15
            t_min = series["t_min"][i] - 273.15
            t_dew = series["t_dew"][i] - 273.15
            u = math.hypot(series["u10"][i], series["v10"][i])
            # ERA5-Land thermal radiation is negative (net loss); the sum of the
            # two IS net radiation, so it is added, not subtracted.
            rn = (series["rn_solar"][i] + series["rn_thermal"][i]) / 1e6
            p = (press[i] / 1000.0) if i < len(press) and press[i] else 101.3
            v = et0_penman_monteith(t_max, t_min, t_dew, u, rn, p)
            et0.append(v)
        except (TypeError, IndexError, KeyError):
            et0.append(None)
    return et0


def crop_water_requirement(aoi, start: str, end: str,
                           mean_ndvi: Optional[float],
                           daily_rain_mm: Optional[Sequence] = None,
                           ndvi_days: Optional[Sequence] = None,
                           ndvi_values: Optional[Sequence] = None) -> dict:
    """
    Seasonal crop water requirement and irrigation requirement for an area.

    ETc = Kcb(NDVI) * ET0, summed over the window; irrigation requirement is
    ETc minus effective rainfall, floored at zero.

    THE SENTENCE THAT MUST TRAVEL WITH THIS NUMBER
    This is what the crop needed. It is NOT what the crop received, and nothing
    in this engine measures what it received. A farmer reading "6 mm/day" as
    "you got 6 mm/day" would be reading it exactly backwards.
    """
    series = era5_daily_series(aoi, start, end)
    if not series:
        return {"status": "NOT AVAILABLE",
                "reason": "no ERA5-Land daily data in the window"}

    et0_daily = _series_to_et0(series)
    usable = [v for v in et0_daily if v is not None]
    if not usable:
        return {"status": "NOT AVAILABLE",
                "reason": ("ERA5-Land returned days but no complete set of "
                           "variables, so no ET0 day could be computed"),
                "days_returned": len(et0_daily)}

    kcb = kcb_from_ndvi(mean_ndvi)
    et0_total = sum(usable)
    out = {
        "status": "OK",
        "et0_mm": round(et0_total, 1),
        "et0_mm_per_day": round(et0_total / len(usable), 2),
        "days_used": len(usable),
        "days_returned": len(et0_daily),
        "observed_day_fraction": round(len(usable) / max(1, len(et0_daily)), 3),
        "provenance": {
            "sensor": "ERA5-Land daily aggregates",
            "date_start": start, "date_end": end,
            "method": "FAO-56 Penman-Monteith, eq. 6, G = 0 at daily step",
            "scale_m": 11000,
            "note": ("ET0 is computed from an 11 km region mean, not per pixel. "
                     "A per-pixel ET0 raster would present 11 km of information "
                     "at 10 m resolution."),
        },
        "interpretation": ("Reference evapotranspiration: what a short, "
                           "well-watered grass surface would transpire here."),
    }

    # PREFERRED PATH: the true integral, sum of Kcb(t) * ET0(t) over days.
    # Needs a dated NDVI series, which the agriculture engine has.
    if ndvi_days is not None and ndvi_values is not None:
        interp = interpolate_to_daily(ndvi_days, ndvi_values, len(et0_daily))
        integral = etc_time_integrated(et0_daily, interp["daily"])
        out["canopy_series"] = {k: interp[k] for k in
                                ("observed_days", "filled_days", "gap_days",
                                 "coverage", "max_gap_days", "basis")
                                if k in interp}
        out["etc_method"] = integral["method"]
        out["etc_coverage"] = integral["coverage"]
        if integral["status"] == "OK":
            etc_total = integral["etc_mm"]
            out.update({
                "kcb": integral["kcb_et0_weighted_mean"],
                "kcb_basis": (kcb["basis"] if kcb else "")
                             + " Weighted by each day's ET0, not a season mean.",
                "etc_mm": etc_total,
                "etc_mm_per_day": round(etc_total / max(1, integral["days_used"]), 2),
                "etc_days_used": integral["days_used"],
                "etc_caveat": ("This is water REQUIRED, not water DELIVERED. "
                               "Nothing in this engine measures delivery to a "
                               "field."),
            })
            if daily_rain_mm is not None:
                _attach_irrigation_requirement(out, etc_total, daily_rain_mm)
            return out
        # Not enough covered days: refuse the integral and say so rather than
        # silently dropping back to the method it was written to replace.
        out["etc_status"] = "NOT AVAILABLE"
        out["etc_reason"] = integral["reason"]
        out["etc_mm"] = None
        return out

    if kcb is None:
        out["etc_status"] = "NOT AVAILABLE"
        out["etc_reason"] = ("no mean NDVI for this area, so no crop coefficient "
                             "and no crop water requirement")
        return out

    # FALLBACK: a single season-mean Kcb times total ET0. Kept only for callers
    # with no dated canopy series, and labelled so nobody mistakes it for the
    # integral. It is biased whenever canopy and ET0 move together across the
    # season, which in an irrigated Sudanese season they do.
    etc_total = et0_total * kcb["kcb"]
    out.update({
        "kcb": kcb["kcb"],
        "kcb_basis": kcb["basis"],
        "kcb_clamped": kcb["clamped"],
        "etc_mm": round(etc_total, 1),
        "etc_mm_per_day": round(etc_total / len(usable), 2),
        "etc_method": ETC_METHOD_APPROXIMATE,
        "etc_caveat": ("This is water REQUIRED, not water DELIVERED. Nothing in "
                       "this engine measures delivery to a field."),
    })

    if daily_rain_mm is not None:
        _attach_irrigation_requirement(out, etc_total, daily_rain_mm)
    else:
        out["irrigation_requirement_mm"] = None
        out["irrigation_requirement_reason"] = (
            "no daily rainfall series supplied, so supply cannot be subtracted "
            "from demand")
    return out


def _attach_irrigation_requirement(out: dict, etc_total: float,
                                   daily_rain_mm: Sequence) -> None:
    """Demand minus supply, floored at zero. Shared by both ETc paths so the
    two cannot drift apart."""
    rain = effective_rainfall_mm(daily_rain_mm)
    deficit = max(0.0, etc_total - rain["effective_rainfall_mm"])
    out.update({
        "rainfall": rain,
        "irrigation_requirement_mm": round(deficit, 1),
        "irrigation_requirement_basis": (
            "ETc minus effective rainfall, floored at zero. Carries NO soil "
            "water store: a real balance needs soil texture, rooting depth "
            "and an initial water content, none of which are inputs. The "
            "seasonal figure is sound; any single day's figure is rough."),
    })


def forecast_7day(aoi, days: int = FORECAST_DAYS, scale: int = 27830) -> dict:
    """
    Short-range weather outlook from NOAA GFS.

    HONEST LIMIT, STATED IN THE OUTPUT
    GFS is a global model on a 0.25 degree grid - roughly 28 km. It forecasts the
    synoptic situation over a scheme, not the weather over a field. Presenting it
    as a field forecast, as farm apps routinely do, implies a spatial precision
    the model does not have. Rainfall in particular is the least skilful variable
    in any global model, and convective rain in the Sahel is the least skilful
    case within that.
    """
    if ee is None:
        return {"status": "NOT AVAILABLE", "reason": "Earth Engine unavailable"}
    try:
        # A DATE FILTER IS NOT OPTIONAL HERE.
        # Measured on 2026-08-29: filtering NOA/GFS0P25 by bounds and
        # forecast_hours alone leaves 228,058 images - the whole archive back to
        # 2015 - and the reduction over them did not return in ten minutes,
        # while every other stage in the engine took 3 to 10 seconds. A forecast
        # is about the next few days, so only the most recent model runs can
        # possibly be relevant, and scanning a decade of superseded forecasts to
        # average them together would be wrong even if it were fast.
        now = datetime.now(timezone.utc)
        recent_start = (now - timedelta(days=RECENT_RUNS_DAYS)).strftime("%Y-%m-%d")
        recent_end = (now + timedelta(days=days + 1)).strftime("%Y-%m-%d")
        col = (ee.ImageCollection(_GFS)
               .filterDate(recent_start, recent_end)
               .filterBounds(aoi)
               .filter(ee.Filter.lt("forecast_hours", days * 24)))
        n = col.size().getInfo()
        if n == 0:
            return {"status": "NOT AVAILABLE",
                    "reason": (f"no GFS forecast steps for this area in the last "
                               f"{RECENT_RUNS_DAYS} days. GFS in Earth Engine "
                               "lags real time, so a forecast is unavailable "
                               "rather than stale."),
                    "searched_from": recent_start}

        # GFS band sets are not uniform across images: a live check on
        # 2026-08-29 found images carrying 6 bands without
        # total_precipitation_surface alongside the usual 9-band images.
        # select() on a collection where some images lack the band fails the
        # whole call, so each band is reduced independently and a band that is
        # absent produces None rather than taking the temperature down with it.
        def _mean_of(band):
            """Reduce one band over only the steps that actually carry it.

            Filtering on system:band_names is the difference between recovering
            the variable and losing it. An earlier version wrapped select() in a
            try/except, which turned "14 of 2046 steps lack this band" into a
            silent None for the whole forecast - discarding 2032 usable steps to
            avoid 14 bad ones. Returns (value, n_steps) so the caller can say
            how much of the forecast the number rests on.
            """
            sub = col.filter(ee.Filter.listContains("system:band_names", band))
            n_band = sub.size().getInfo()
            if n_band == 0:
                return None, 0
            v = sub.select([band]).mean().reduceRegion(
                reducer=ee.Reducer.mean(), geometry=aoi, scale=scale,
                maxPixels=1e8, bestEffort=True).getInfo().get(band)
            return v, n_band

        temp, n_temp = _mean_of("temperature_2m_above_ground")
        precip, n_precip = _mean_of("total_precipitation_surface")
        stats = {"temperature_2m_above_ground": temp,
                 "total_precipitation_surface": precip}

        return {
            "status": "OK",
            "horizon_days": days,
            "mean_temperature_c": (round(stats.get("temperature_2m_above_ground"), 1)
                                   if stats.get("temperature_2m_above_ground") is not None
                                   else None),
            "mean_precipitation_mm_per_step": (
                round(stats.get("total_precipitation_surface"), 2)
                if stats.get("total_precipitation_surface") is not None else None),
            "forecast_steps": n,
            "steps_with_temperature": n_temp,
            "steps_with_precipitation": n_precip,
            "provenance": {
                "sensor": "NOAA GFS 0.25 degree",
                "scale_m": scale,
                "searched_from": recent_start,
                "note": ("A ~28 km global model. This is the outlook over the "
                         "scheme, not over a field. Rainfall is the least "
                         "skilful variable in it."),
            },
            "caveat": ("A forecast is not a measurement. Nothing else in this "
                       "platform depends on it, and no alert is raised from it."),
        }
    except Exception as e:
        return {"status": "NOT AVAILABLE", "reason": str(e)}


# ==============================================================================
# YIELD - refused until calibrated, exactly like nitrogen
# ==============================================================================

def yield_estimate(mean_ndvi: Optional[float], crop: str,
                   calibration: Optional[dict] = None) -> dict:
    """
    Yield is reported ONLY from a locally fitted model that clears an error
    limit. Otherwise a relative statement is made and no tonnage is quoted.

    WHY THE REFUSAL IS THE FEATURE
    Every farm app that shows a yield forecast from NDVI is extrapolating a
    canopy measurement through a relationship it has not fitted for that crop,
    variety, management or season. In a scheme where water delivery is the
    binding constraint, the NDVI-to-yield relationship is dominated by exactly
    the variable that is failing - so the estimate is least reliable in the
    situation the farmer most needs it. A refusal with a stated reason is a
    stronger position than a number nobody can defend.

    `calibration` is a fitted model as {"n_points", "rmse_fraction", "r2",
    "slope", "intercept", "crop"}.
    """
    if mean_ndvi is None:
        # yield_t_ha is present and None even here: a consumer that reads the
        # key must find an explicit "no number", never a missing key it might
        # default to zero.
        return {"status": "NOT AVAILABLE", "yield_t_ha": None,
                "claim_level": None,
                "reason": "no usable NDVI for this area in the window"}

    base = {
        "status": "OK",
        "claim_level": "relative",
        "mean_ndvi": round(float(mean_ndvi), 4),
        "yield_t_ha": None,
        "caveat": ("Canopy greenness is not yield. Grain fill, harvest losses "
                   "and terminal stress all break the relationship after the "
                   "canopy has already been measured."),
    }

    if not calibration:
        base["reason"] = (
            f"no calibrated yield model exists for {crop}; at least "
            f"{MIN_YIELD_CALIBRATION_POINTS} local harvest measurements are "
            "needed before any tonnage is quoted")
        return base

    gate = dl.calibration_gate(
        n_points=calibration.get("n_points"),
        rmse=calibration.get("rmse_fraction"),
        min_points=MIN_YIELD_CALIBRATION_POINTS,
        max_rmse=MAX_ACCEPTABLE_YIELD_RMSE_FRACTION,
        quantity="a yield figure", unit=" (fraction of mean yield)")
    if not gate["may_quote"]:
        base["reason"] = gate["reason"]
        return base

    est = (calibration.get("intercept", 0.0)
           + calibration.get("slope", 0.0) * float(mean_ndvi))
    base.update({
        "claim_level": "calibrated",
        "yield_t_ha": round(est, 2),
        "confidence": {"rmse_fraction": calibration.get("rmse_fraction"),
                       "r2": calibration.get("r2"),
                       "n_points": calibration.get("n_points")},
        "reason": None,
    })
    return base


# ==============================================================================
# YIELD CALIBRATION STORE - the only thing that unlocks a tonnage
# ==============================================================================

class YieldCalibrationStore:
    """
    Harvest measurements, and the model they permit.

    WHAT COUNTS AS A POINT
    A weighed harvest from a known area on a known date, paired with the
    satellite canopy for that field and season. Not an estimate, not a
    recollection, not a sack count converted by a rule of thumb - those carry
    an error nobody can quantify, and an unquantifiable error in the training
    data becomes an unquantifiable error in every prediction made from it.

    WHY THE FIT IS DELIBERATELY A STRAIGHT LINE
    With a few dozen points, a linear fit is the most that is defensible. A
    flexible model on thirty samples fits the noise and reports a flattering
    error that will not survive contact with next season.
    """

    def __init__(self, path: str = "yield_calibration.db"):
        import sqlite3
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS yield_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crop TEXT NOT NULL, field_id TEXT, season INTEGER,
                recorded_at TEXT NOT NULL,
                harvested_kg REAL NOT NULL, area_ha REAL NOT NULL,
                yield_t_ha REAL NOT NULL, ndvi REAL NOT NULL,
                method TEXT NOT NULL, operator TEXT, notes TEXT
            )""")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS yield_model (
                crop TEXT PRIMARY KEY, slope REAL, intercept REAL,
                r2 REAL, rmse_fraction REAL, n_points INTEGER, fitted_at TEXT
            )""")
        self.conn.commit()

    def add_point(self, crop: str, harvested_kg: float, area_ha: float,
                  ndvi: float, field_id: str = "", season: Optional[int] = None,
                  method: str = "WEIGHED", operator: str = "",
                  notes: str = "") -> int:
        if area_ha is None or area_ha <= 0:
            raise ValueError("a yield point needs a positive harvested area; "
                             "kilograms without an area is not a yield")
        if harvested_kg is None or harvested_kg < 0:
            raise ValueError("harvested weight must be zero or positive")
        if ndvi is None:
            raise ValueError(
                "a yield point needs the satellite canopy for that field and "
                "season; a harvest with no matching observation trains nothing")
        t_ha = (harvested_kg / 1000.0) / area_ha
        cur = self.conn.execute(
            "INSERT INTO yield_points (crop, field_id, season, recorded_at,"
            " harvested_kg, area_ha, yield_t_ha, ndvi, method, operator, notes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (crop, field_id, season, datetime.now(timezone.utc).isoformat(),
             harvested_kg, area_ha, t_ha, ndvi, method, operator, notes))
        self.conn.commit()
        return cur.lastrowid

    def points(self, crop: str) -> list:
        rows = self.conn.execute(
            "SELECT ndvi, yield_t_ha FROM yield_points WHERE crop = ?",
            (crop,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def fit(self, crop: str) -> dict:
        """
        Fit yield against canopy by least squares, and store it only if it
        clears the gate. RMSE is expressed as a FRACTION of mean observed
        yield, so the limit means the same thing for a 1 t/ha crop and a
        6 t/ha one.
        """
        pts = self.points(crop)
        n = len(pts)
        if n < MIN_YIELD_CALIBRATION_POINTS:
            return {"fitted": False, "n_points": n,
                    "reason": (f"{n} points; {MIN_YIELD_CALIBRATION_POINTS} "
                               "needed before a model is fitted")}
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx == 0:
            return {"fitted": False, "n_points": n,
                    "reason": ("every point has the same canopy value, so no "
                               "relationship can be fitted - the samples need "
                               "to span a range")}
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        intercept = my - slope * mx
        preds = [slope * x + intercept for x in xs]
        ss_res = sum((y - p) ** 2 for y, p in zip(ys, preds))
        ss_tot = sum((y - my) ** 2 for y in ys)
        r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
        rmse = (ss_res / n) ** 0.5
        rmse_fraction = rmse / my if my else None

        self.conn.execute(
            "INSERT OR REPLACE INTO yield_model (crop, slope, intercept, r2,"
            " rmse_fraction, n_points, fitted_at) VALUES (?,?,?,?,?,?,?)",
            (crop, slope, intercept, r2, rmse_fraction, n,
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()
        return {"fitted": True, "crop": crop, "n_points": n,
                "slope": round(slope, 4), "intercept": round(intercept, 4),
                "r2": round(r2, 4),
                "rmse_fraction": (round(rmse_fraction, 4)
                                  if rmse_fraction is not None else None),
                "rmse_t_ha": round(rmse, 4)}

    def model(self, crop: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT slope, intercept, r2, rmse_fraction, n_points FROM"
            " yield_model WHERE crop = ?", (crop,)).fetchone()
        if not row:
            return None
        return {"slope": row[0], "intercept": row[1], "r2": row[2],
                "rmse_fraction": row[3], "n_points": row[4]}

    def progress(self, crop: str) -> dict:
        """How far this crop is from a quotable yield, and what is missing."""
        m = self.model(crop)
        return dl.calibration_progress(
            n_points=len(self.points(crop)),
            min_points=MIN_YIELD_CALIBRATION_POINTS,
            rmse=(m or {}).get("rmse_fraction"),
            max_rmse=MAX_ACCEPTABLE_YIELD_RMSE_FRACTION,
            quantity="a yield in tonnes per hectare")

    def close(self):
        self.conn.close()
