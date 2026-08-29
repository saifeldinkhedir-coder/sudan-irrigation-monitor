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
from datetime import datetime
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

def era5_daily_series(aoi, start: str, end: str, scale: int = 11000) -> Optional[dict]:
    """
    Pull the daily ERA5-Land variables ET0 needs as region means, in ONE
    aggregate_array round trip per variable rather than one per day.

    Returns None when the collection is empty, so the caller says NOT AVAILABLE.
    """
    if ee is None:
        return None
    try:
        col = ee.ImageCollection(_ERA5_DAILY).filterBounds(aoi).filterDate(start, end)
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
            stats = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=aoi,
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
                           daily_rain_mm: Optional[Sequence] = None) -> dict:
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

    if kcb is None:
        out["etc_status"] = "NOT AVAILABLE"
        out["etc_reason"] = ("no mean NDVI for this area, so no crop coefficient "
                             "and no crop water requirement")
        return out

    etc_total = et0_total * kcb["kcb"]
    out.update({
        "kcb": kcb["kcb"],
        "kcb_basis": kcb["basis"],
        "kcb_clamped": kcb["clamped"],
        "etc_mm": round(etc_total, 1),
        "etc_mm_per_day": round(etc_total / len(usable), 2),
        "etc_caveat": ("This is water REQUIRED, not water DELIVERED. Nothing in "
                       "this engine measures delivery to a field."),
    })

    if daily_rain_mm is not None:
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
    else:
        out["irrigation_requirement_mm"] = None
        out["irrigation_requirement_reason"] = (
            "no daily rainfall series supplied, so supply cannot be subtracted "
            "from demand")
    return out


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
        col = (ee.ImageCollection(_GFS)
               .filterBounds(aoi)
               .filter(ee.Filter.lt("forecast_hours", days * 24)))
        n = col.size().getInfo()
        if n == 0:
            return {"status": "NOT AVAILABLE",
                    "reason": "no GFS forecast steps available for this area"}

        # GFS band sets are not uniform across images: a live check on
        # 2026-08-29 found images carrying 6 bands without
        # total_precipitation_surface alongside the usual 9-band images.
        # select() on a collection where some images lack the band fails the
        # whole call, so each band is reduced independently and a band that is
        # absent produces None rather than taking the temperature down with it.
        def _mean_of(band):
            try:
                sub = col.select([band])
                return sub.mean().reduceRegion(
                    reducer=ee.Reducer.mean(), geometry=aoi, scale=scale,
                    maxPixels=1e8, bestEffort=True).getInfo().get(band)
            except Exception:
                return None

        stats = {"temperature_2m_above_ground":
                 _mean_of("temperature_2m_above_ground"),
                 "total_precipitation_surface":
                 _mean_of("total_precipitation_surface")}

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
            "provenance": {
                "sensor": "NOAA GFS 0.25 degree",
                "scale_m": scale,
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
