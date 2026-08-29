"""
The rest of the network layer: continuity, siltation proxies, and efficiency.

WHAT THIS ADDS TO canal_water_status AND head_tail_equity
---------------------------------------------------------
The engine could already say "there was water somewhere along this canal" and
"the tail grew less than the head". Three questions in the original scope were
still unanswered:

  CONTINUITY   Not "was there water", but WHERE it stopped. A canal wet at the
               head and dry from kilometre 7 onward is a different problem from
               one uniformly half-wet, and the two produce the same seasonal
               average. Continuity is the number that turns "the tail is doing
               badly" into "the water reached reach 4 and no further".

  SILTATION    Whether a reach is carrying less water than it used to. Measured
               as a change in wetted extent at the SAME reach across seasons,
               which is the only form of the question a satellite can address.

  EFFICIENCY   ET consumed against water released. This one is refused, loudly
               and by default, because the denominator is not a satellite
               measurement and nobody here has it.

THE ONE THING RADAR CANNOT DO, RESTATED
---------------------------------------
Every figure in this module is built on STANDING WATER. C-band backscatter tells
you a surface is smooth and wet; it does not tell you the water is moving, that
it is moving in the intended direction, or that anyone downstream can use it. A
canal that is full and static reads identically to one carrying its design
discharge. Nothing here should ever be described as flow, and the word does not
appear in any output string in this file.

A SECOND LIMIT THAT MATTERS MORE IN GEZIRA THAN IN NEW HALFA
------------------------------------------------------------
A Gezira minor canal is roughly 5-15 m wide. At 10 m, that is a sub-pixel to
one-pixel target, and a mixed pixel of water plus bank plus vegetation does not
cross the water threshold. So on minor canals these figures will be weak or
absent, and that is a true statement about resolution rather than about water.
The functions below report the width they can and cannot resolve rather than
returning a confident zero.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import decision_logic as dl

try:
    import ee
except ImportError:
    ee = None


# ==============================================================================
# CONFIGURATION - every hand-chosen number, declared
# ==============================================================================

# Sentinel-1 VV below this (dB) is treated as water. ARBITRARY, and the same
# value the engine uses; kept here so this module can be read on its own.
S1_WATER_DB = -16.0

# A reach counts as WET when at least this fraction of its buffered footprint
# reads as water. ARBITRARY. It is the single most consequential constant in
# this file: it decides where a continuity break is reported.
REACH_WET_FRACTION = 0.15

# Sampling buffer either side of the canal centreline, metres. Wide enough to
# survive geometry error in a traced centreline, narrow enough to stay off the
# adjacent fields, which flood and would read as canal water.
CANAL_BUFFER_M = 30

# Below this the continuity picture is a handful of noisy snapshots rather than
# a season. ARBITRARY.
MIN_S1_SCENES = 4

# Canal widths, in metres, against the 10 m Sentinel-1 pixel. These are not
# thresholds the code applies; they are the honesty statement it attaches.
RESOLVABLE_WIDTH_M = 20.0

# Siltation: a drop in wetted fraction at the same reach across seasons is
# reported as a candidate when it exceeds this. ARBITRARY, and deliberately
# large - small inter-season differences are dominated by acquisition geometry,
# incidence angle and vegetation on the banks, not by sediment.
SILTATION_DROP_FRACTION = 0.25
MIN_SEASONS_FOR_TREND = 3


# ==============================================================================
# PURE LOGIC
# ==============================================================================

def continuity_profile(reach_wet_fractions: Sequence[Optional[float]],
                       wet_threshold: float = REACH_WET_FRACTION) -> dict:
    """
    Where along the canal the water stops.

    Takes the wet fraction of each reach in head-to-tail order and returns the
    first break, the longest dry run, and how far down the canal water was
    detected at all.

    WHY A PROFILE AND NOT AN AVERAGE
    A canal wet at reaches 1-3 and dry at 4-8 and a canal half-wet everywhere
    have the same mean. They are completely different operational problems, and
    only one of them points at a location a person can go and look at.

    Reaches whose value is None are UNOBSERVED, not dry. They break the run
    rather than extending it, because calling an unobserved reach dry would
    manufacture the exact finding this function exists to report.
    """
    n = len(reach_wet_fractions)
    if n == 0:
        return {"status": "NOT AVAILABLE", "reason": "no reaches supplied"}

    observed = [i for i, v in enumerate(reach_wet_fractions) if v is not None]
    if not observed:
        return {"status": "NOT AVAILABLE",
                "reason": "no reach produced a usable radar measurement",
                "n_reaches": n}

    states = ["UNOBSERVED" if v is None
              else ("WET" if v >= wet_threshold else "DRY")
              for v in reach_wet_fractions]

    first_dry = next((i for i, s in enumerate(states) if s == "DRY"), None)
    last_wet = max((i for i, s in enumerate(states) if s == "WET"), default=None)

    # longest run of consecutive DRY reaches; UNOBSERVED breaks a run
    longest_dry, run = 0, 0
    for s in states:
        run = run + 1 if s == "DRY" else 0
        longest_dry = max(longest_dry, run)

    wet_count = states.count("WET")
    dry_count = states.count("DRY")
    unobserved = states.count("UNOBSERVED")

    return {
        "status": "OK",
        "n_reaches": n,
        "states": states,
        "wet_reaches": wet_count,
        "dry_reaches": dry_count,
        "unobserved_reaches": unobserved,
        "first_dry_reach": None if first_dry is None else first_dry + 1,
        "last_wet_reach": None if last_wet is None else last_wet + 1,
        "longest_dry_run": longest_dry,
        # Water was detected in an unbroken sequence from the head down to the
        # furthest wet reach: no DRY reach sits between them. An UNOBSERVED
        # reach in that span leaves this None - we cannot claim continuity
        # across a reach we did not see.
        "continuous_to_last_wet": (
            None if last_wet is None
            else (None if "UNOBSERVED" in states[:last_wet]
                  else "DRY" not in states[:last_wet])),
        "wet_threshold": wet_threshold,
        "threshold_basis": ("ARBITRARY: the wet-fraction cut-off decides where a "
                            "break is reported and has no hydraulic meaning."),
        "interpretation": (
            "Standing water detected per reach, head to tail. A break says "
            "water was not detected beyond that point in this window; it does "
            "not say why, and it does not describe movement of water."),
        "unobserved_note": (
            "Unobserved reaches are not dry reaches. They interrupt a run "
            "rather than extending it."),
    }


def siltation_candidates(seasonal_wet_by_reach: dict,
                         drop_threshold: float = SILTATION_DROP_FRACTION,
                         min_seasons: int = MIN_SEASONS_FOR_TREND) -> dict:
    """
    Reaches whose wetted extent has fallen across seasons.

    `seasonal_wet_by_reach` maps reach number -> {season_year: wet_fraction}.

    WHY THIS IS A CANDIDATE LIST AND NOT A SILTATION MEASUREMENT
    A reach that holds less water than it did three seasons ago may be silted.
    It may equally have been operated differently, received a different
    allocation, been deliberately closed, grown vegetation on its banks that
    changed the backscatter, or been imaged at a different incidence angle. This
    function separates none of those. It produces a list of places worth
    inspecting, which is a genuinely useful output, and it is not evidence of
    channel degradation.

    Sediment depth is not observable from orbit at all. Nothing here estimates
    it, and no output implies a volume.
    """
    if not seasonal_wet_by_reach:
        return {"status": "NOT AVAILABLE", "reason": "no seasonal data supplied"}

    candidates, examined, skipped = [], 0, []
    for reach, by_season in sorted(seasonal_wet_by_reach.items()):
        pairs = sorted((int(y), v) for y, v in by_season.items() if v is not None)
        if len(pairs) < min_seasons:
            skipped.append({"reach": reach, "seasons": len(pairs)})
            continue
        examined += 1
        years = [p[0] for p in pairs]
        vals = [p[1] for p in pairs]
        early = sum(vals[:len(vals) // 2]) / max(1, len(vals) // 2)
        late = sum(vals[len(vals) // 2:]) / max(1, len(vals) - len(vals) // 2)
        drop = (early - late) / early if early > 0 else 0.0
        if drop >= drop_threshold:
            candidates.append({
                "reach": reach,
                "earlier_mean_wet_fraction": round(early, 4),
                "recent_mean_wet_fraction": round(late, 4),
                "relative_drop": round(drop, 3),
                "seasons": years,
            })

    return {
        "status": "OK",
        "candidates": candidates,
        "reaches_examined": examined,
        "reaches_skipped_too_few_seasons": skipped,
        "drop_threshold": drop_threshold,
        "min_seasons": min_seasons,
        "threshold_basis": (
            "ARBITRARY: deliberately large. Small inter-season differences are "
            "dominated by acquisition geometry, incidence angle and bank "
            "vegetation, not by sediment."),
        "interpretation": (
            "Reaches holding visibly less standing water than in earlier "
            "seasons. This is a list of places worth inspecting on the ground. "
            "Siltation, a different operating pattern, a different allocation, "
            "deliberate closure and changed bank vegetation all produce this "
            "signal, and none of them is separated here."),
        "not_measured": (
            "Sediment depth is not observable from orbit. No volume, depth or "
            "rate of degradation is estimated anywhere in this result."),
    }


def water_use_efficiency(et_consumed_mm: Optional[float],
                         command_area_ha: Optional[float],
                         water_released_m3: Optional[float] = None) -> dict:
    """
    Water consumed against water released - and the refusal when the denominator
    is missing, which is the normal case.

    THE DENOMINATOR IS NOT A SATELLITE MEASUREMENT
    Efficiency is a ratio. The numerator (evapotranspiration over the command)
    comes from MODIS and can be estimated. The denominator (volume released to
    the command through the offtake) comes from the scheme authority's gauge
    readings or its operating records. If those do not exist, or are not shared,
    then efficiency cannot be computed - not approximately, not with a caveat,
    not at all.

    A great many irrigation studies quietly substitute a design discharge or an
    allocation figure for the measured release. That converts a measurement into
    an assumption while keeping the word "efficiency" on it, and it produces
    numbers that look like performance findings about real canals. This function
    refuses instead, and reports the consumption it CAN measure so the useful
    half is not lost with the impossible half.
    """
    if et_consumed_mm is None or command_area_ha is None:
        return {"status": "NOT AVAILABLE",
                "reason": ("needs both an ET figure and a command area; one or "
                           "both is missing"),
                "efficiency": None}

    # 1 mm over 1 ha = 10 m3
    consumed_m3 = et_consumed_mm * command_area_ha * 10.0
    out = {
        "status": "OK",
        "et_consumed_mm": round(et_consumed_mm, 1),
        "command_area_ha": round(command_area_ha, 1),
        "consumed_m3": round(consumed_m3, 1),
        "efficiency": None,
        "provenance_kind": "MEASURED (numerator only)",
        "interpretation": (
            "Water consumed by evapotranspiration over the command area. This "
            "is consumption, not efficiency."),
    }

    if water_released_m3 is None:
        out["efficiency_status"] = "NOT AVAILABLE"
        out["efficiency_reason"] = (
            "no measured release volume for this command. Efficiency needs a "
            "denominator that comes from the scheme authority's gauge or "
            "operating records, not from a satellite. A design discharge or an "
            "allocation figure is not a measured release and is not substituted "
            "here.")
        return out

    if water_released_m3 <= 0:
        out["efficiency_status"] = "NOT AVAILABLE"
        out["efficiency_reason"] = "supplied release volume is zero or negative"
        return out

    ratio = consumed_m3 / water_released_m3
    out.update({
        "efficiency_status": "OK",
        "water_released_m3": round(water_released_m3, 1),
        "efficiency": round(ratio, 3),
        "provenance_kind": "MIXED",
        "provenance_detail": {
            "numerator": "MEASURED: MODIS actual evapotranspiration",
            "denominator": "REPORTED: release volume supplied by the operator",
        },
        "efficiency_caveat": (
            "A ratio above 1 means the command consumed more than the recorded "
            "release, which points at rainfall, groundwater, an unrecorded "
            "release or a wrong area - not at a physical impossibility. The "
            "denominator is reported by a person and is not verified here."),
    })
    return out


def resolvability_note(canal_width_m: Optional[float],
                       pixel_m: float = 10.0) -> dict:
    """
    What this canal's width means for every radar figure attached to it.

    Attached to results rather than used as a filter: a narrow canal still gets
    its numbers, with the statement that they are unreliable, because silently
    dropping narrow canals would make a scheme's most numerous canals vanish
    from a report without explanation.
    """
    if canal_width_m is None:
        return {"resolvable": None,
                "note": ("canal width not supplied; radar figures for this canal "
                         "cannot be qualified and should be treated as "
                         "unverified")}
    pixels = canal_width_m / pixel_m
    if canal_width_m >= RESOLVABLE_WIDTH_M:
        verdict, note = True, (
            f"{canal_width_m} m is about {pixels:.1f} pixels at {pixel_m} m. "
            "Radar water figures for this canal are meaningful.")
    elif canal_width_m >= pixel_m:
        verdict, note = False, (
            f"{canal_width_m} m is about {pixels:.1f} pixels at {pixel_m} m. "
            "Every pixel mixes water with bank and vegetation, so the water "
            "fraction is biased low and a dry reading may only mean a narrow "
            "canal.")
    else:
        verdict, note = False, (
            f"{canal_width_m} m is below one {pixel_m} m pixel. Radar cannot "
            "resolve standing water in this canal at all; an absent water "
            "signal here is a statement about resolution, not about water.")
    return {"resolvable": verdict, "width_m": canal_width_m,
            "pixels_across": round(pixels, 2), "note": note,
            "threshold_m": RESOLVABLE_WIDTH_M,
            "threshold_basis": "ARBITRARY: two pixels across as a usability floor"}


# ==============================================================================
# EARTH ENGINE
# ==============================================================================

def reach_wet_fractions(canal_geom, start: str, end: str,
                        n_reaches: int = 6,
                        reverse: bool = False) -> dict:
    """
    Wet fraction per reach, head to tail, in one pass.

    `reverse` comes from decision_logic.resolve_canal_direction via the caller.
    Getting it wrong would report a break at the head that is really at the
    tail, so this function does not decide direction on its own and requires the
    caller to have resolved it.
    """
    if ee is None:
        return {"status": "NOT AVAILABLE", "reason": "Earth Engine unavailable"}
    try:
        coords = canal_geom.coordinates().getInfo()
        if not coords or len(coords) < 2:
            return {"status": "NOT AVAILABLE",
                    "reason": "canal geometry has too few vertices to split"}
        if reverse:
            coords = list(reversed(coords))

        buf = canal_geom.buffer(CANAL_BUFFER_M)
        col = (ee.ImageCollection("COPERNICUS/S1_GRD")
               .filterBounds(buf).filterDate(start, end)
               .filter(ee.Filter.listContains(
                   "transmitterReceiverPolarisation", "VV"))
               .filter(ee.Filter.eq("instrumentMode", "IW"))
               .select("VV"))
        n = col.size().getInfo()
        if n < MIN_S1_SCENES:
            season_year = int(start[:4]) if start[:4].isdigit() else None
            avail = dl.sentinel1_availability(n, season_year, MIN_S1_SCENES)
            return {"status": "INSUFFICIENT DATA",
                    "reason": avail["reason"],
                    "remedy": avail.get("remedy"),
                    "cause": avail.get("cause"),
                    "n_scenes": n}

        water = col.median().lt(S1_WATER_DB)
        step = max(1, len(coords) // n_reaches)
        fractions = []
        for i in range(0, len(coords) - 1, step):
            seg = coords[i:min(i + step + 1, len(coords))]
            if len(seg) < 2:
                continue
            seg_buf = ee.Geometry.LineString(seg).buffer(CANAL_BUFFER_M)
            v = water.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=seg_buf, scale=10,
                maxPixels=1e9, bestEffort=True).getInfo().get("VV")
            fractions.append(v)

        return {"status": "OK", "fractions": fractions, "n_scenes": n,
                "provenance": {"sensor": "Sentinel-1 VV (IW, GRD) median",
                               "date_start": start, "date_end": end,
                               "n_scenes": n, "scale_m": 10,
                               "buffer_m": CANAL_BUFFER_M,
                               "water_cutoff_db": S1_WATER_DB,
                               "cutoff_basis": "ARBITRARY"}}
    except Exception as e:
        return {"status": "NOT AVAILABLE", "reason": str(e)}


def canal_continuity(canal_geom, start: str, end: str, n_reaches: int = 6,
                     reverse: bool = False,
                     canal_width_m: Optional[float] = None) -> dict:
    """Full continuity record for one canal: where water was detected, where it
    was not, and what the canal's width means for believing any of it."""
    reaches = reach_wet_fractions(canal_geom, start, end, n_reaches, reverse)
    if reaches["status"] != "OK":
        return {"status": reaches["status"], "reason": reaches.get("reason")}
    profile = continuity_profile(reaches["fractions"])
    profile["reach_wet_fractions"] = [
        None if v is None else round(v, 4) for v in reaches["fractions"]]
    profile["provenance"] = reaches["provenance"]
    profile["resolvability"] = resolvability_note(canal_width_m)
    return profile
