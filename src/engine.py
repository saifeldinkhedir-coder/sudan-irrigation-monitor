"""
Sudan Irrigation & Agriculture Monitor - analysis engine.

WHAT THIS IS
------------
A multi-sensor Google Earth Engine engine that answers two questions usually
asked separately:

  FIELD    How is this field doing, and is it short of water?
  NETWORK  Did the water actually arrive, and was it shared fairly from source
           to farm?

Field-scale crop monitoring is a solved category. The network layer is the part
almost nobody does, and it is where Sudan's irrigation problem actually lives.
The farmer at the tail of a gravity-fed canal already knows the crop is
suffering; what they cannot see is whether their reach received systematically
less than the head, or whether the cause was drought rather than a network
fault. That is what this engine measures.

WHAT CHANGED FROM THE FIRST DRAFT (and why)
-------------------------------------------
1. --command-areas is now actually used. The first draft parsed it and then
   analysed a synthetic 1.5 km buffer regardless, silently discarding the real
   geometry - the exact "quiet substitution" the integrity rules forbid. Now,
   when polygons are supplied, each canal is matched to its command area (by a
   shared 'canal' property, else by spatial intersection) and that geometry is
   used. When none is supplied the buffer is used AND the provenance says so.
2. Irrigated extent uses an Otsu split of the NDVI histogram, not median+k*sigma.
   A command area is a bimodal cropped/bare mixture, not one hump with noise;
   the old statistic mis-classified it and typically called almost nothing
   cropped. The split's valley depth is reported so a weak (near-unimodal) case
   is surfaced rather than hidden.
3. Head-to-tail equity is an OLS slope through ALL reaches with a bootstrap
   confidence interval on the gap, not a first-vs-last difference. Two points
   cannot separate a gradient from noise. A canal is flagged only when we are
   confident the gap is real.
4. Every reported number carries a machine-readable provenance block: sensor,
   dates, scene count, threshold basis, and the fraction of the area actually
   observed.
5. The nutrition and climate layers (nutrition_climate_ground.py) are wired in,
   so a run produces one integrated record instead of three disconnected ones.

The pure decision logic (thresholds, Otsu, slope fit, agreement, the nitrogen
gate) lives in decision_logic.py and is covered by tests. This file is the
Earth Engine plumbing that feeds those decisions.

RUN
---
    python cli.py --canal canals.geojson --command-areas fields.geojson \
                  --season 2024 --crop sorghum --out results.json
"""

from __future__ import annotations

import json
import os
import sys
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
# CONFIGURATION - every hand-chosen number lives here and is declared as such
# ==============================================================================

K_SIGMA = 2.0            # adaptive threshold multiplier (stress indicators only)
SCALE_M = 10             # Sentinel-2 native
SCALE_COARSE_M = 100     # thermal / ET
CLOUD_PCT = 40           # S2 scene filter; the median handles the rest

# Sentinel-1 VV backscatter below this (dB) is treated as open water. Water is
# specular at C-band so it returns very little. ARBITRARY: -16 dB is a common
# literature starting point; the correct value is site-specific and should be
# replaced from ground observation once it exists.
S1_WATER_DB = -16.0

# A canal segment is "wet" when at least this fraction of its buffered footprint
# reads as water. ARBITRARY.
CANAL_WET_FRACTION = 0.15

# Canal buffer for sampling: narrow enough to stay in the canal, wide enough to
# survive geometry error in the network file.
CANAL_BUFFER_M = 30

# Strip either side of the canal used as its command area WHEN no command-area
# polygon is supplied. ARBITRARY and a poor substitute for real geometry.
FALLBACK_COMMAND_HALF_WIDTH_M = 1500

# Rainfall in the preceding window, below which stress is unlikely to be drought.
RAIN_WINDOW_DAYS = 14
RAIN_LOW_MM = 5.0        # ARBITRARY

# Equity: a head/tail gap whose 95% lower bound exceeds this fraction is flagged.
# ARBITRARY - controls how many canals a manager reviews; no regulatory meaning.
EQUITY_GAP_FLAG = 0.20

# Number of reaches a canal is split into for the equity slope fit.
N_REACHES = 6

# Minimum usable observations before an indicator is reported at all.
MIN_S2_SCENES = 3
MIN_S1_SCENES = 2


# ==============================================================================
# RESULT TYPES
# ==============================================================================

@dataclass
class Provenance:
    """Machine-readable record of how a number was produced (integrity rule 7)."""
    sensor: str = ""
    date_start: str = ""
    date_end: str = ""
    n_scenes: Optional[int] = None
    scale_m: Optional[int] = None
    threshold_basis: Optional[str] = None
    observed_fraction: Optional[float] = None   # share of the AOI with valid data
    notes: str = ""


@dataclass
class Indicator:
    """One measured quantity, or an explicit statement that it was not measured."""
    name: str
    status: str                      # OK | NOT AVAILABLE | INSUFFICIENT DATA
    value: Optional[float] = None
    unit: str = ""
    reason: Optional[str] = None
    threshold: Optional[float] = None
    threshold_basis: Optional[str] = None
    interpretation: str = ""
    provenance: Optional[dict] = None

    @classmethod
    def unavailable(cls, name: str, reason: str,
                    provenance: Optional[Provenance] = None) -> "Indicator":
        return cls(name=name, status="NOT AVAILABLE", reason=reason,
                   provenance=asdict(provenance) if provenance else None)


# ==============================================================================
# EARTH ENGINE SETUP
# ==============================================================================

def init_ee() -> str:
    project = os.environ.get("EE_PROJECT")
    if not project and os.path.exists(".env"):
        for line in open(".env", encoding="utf-8"):
            if line.startswith("EE_PROJECT="):
                project = line.split("=", 1)[1].strip()
    if not project:
        print("ABORT: set EE_PROJECT in .env or the environment.")
        sys.exit(1)
    ee.Initialize(project=project)
    return project


def season_window(season: int) -> tuple[str, str]:
    """
    Sudan's main irrigated season runs roughly July to March. Passing a year
    returns that season's window, so a 'season' never splits across two harvests.
    """
    return f"{season}-07-01", f"{season + 1}-03-31"


# ==============================================================================
# SENSOR LAYERS
# ==============================================================================

def s2_collection(aoi, start: str, end: str):
    return (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(aoi).filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CLOUD_PCT)))


def s2_indices(img):
    """
    NDVI  vigour and green biomass
    EVI   less prone to saturating over dense canopy than NDVI
    NDMI  canopy water content - responds to stress earlier than NDVI
    MNDWI open water, used for irrigated-extent and canal work
    """
    b = img.divide(10000)
    ndvi = b.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndmi = b.normalizedDifference(["B8", "B11"]).rename("NDMI")
    mndwi = b.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    evi = b.expression(
        "2.5 * ((N - R) / (N + 6 * R - 7.5 * B + 1))",
        {"N": b.select("B8"), "R": b.select("B4"), "B": b.select("B2")}
    ).rename("EVI")
    return ee.Image.cat([ndvi, evi, ndmi, mndwi])


def s1_collection(aoi, start: str, end: str):
    """Sentinel-1 GRD, VV, IW. The only sensor here that works through cloud and
    dust, so it carries the canal-water question during the rainy season."""
    return (ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(aoi).filterDate(start, end)
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .select("VV"))


def thermal_collection(aoi, start: str, end: str):
    """Landsat 8/9 C2 L2 surface temperature. A transpiring crop is cooler than a
    water-stressed one, so LST is a direct physical stress measure that moves
    before the canopy visibly changes. 100 m, 16-day."""
    def to_celsius(img):
        lst = img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15)
        return lst.rename("LST").copyProperties(img, ["system:time_start"])

    l8 = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
          .filterBounds(aoi).filterDate(start, end)
          .filter(ee.Filter.lt("CLOUD_COVER", 50)))
    l9 = (ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
          .filterBounds(aoi).filterDate(start, end)
          .filter(ee.Filter.lt("CLOUD_COVER", 50)))
    return l8.merge(l9).map(to_celsius)


def rainfall_mm(aoi, start: str, end: str) -> Optional[float]:
    """Total CHIRPS rainfall over the window, in millimetres."""
    col = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
           .filterBounds(aoi).filterDate(start, end))
    if col.size().getInfo() == 0:
        return None
    total = col.sum().reduceRegion(
        reducer=ee.Reducer.mean(), geometry=aoi,
        scale=5000, maxPixels=1e9, bestEffort=True).getInfo()
    return total.get("precipitation")


def evapotranspiration_mm(aoi, start: str, end: str) -> Optional[float]:
    """MODIS actual ET (MOD16A2GF, 8-day, 500 m), mm over the window. Coarse, so
    a canal-command figure only. Its value is that it measures water genuinely
    consumed, which can be set against water released to give an efficiency."""
    try:
        col = (ee.ImageCollection("MODIS/061/MOD16A2GF")
               .filterBounds(aoi).filterDate(start, end).select("ET"))
        if col.size().getInfo() == 0:
            return None
        total = col.sum().multiply(0.1).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=aoi,
            scale=500, maxPixels=1e9, bestEffort=True).getInfo()
        return total.get("ET")
    except Exception:
        return None


def groundwater_context(aoi, start: str, end: str) -> Optional[float]:
    """GRACE / GRACE-FO terrestrial water storage anomaly, cm equivalent water
    height. ~300 km footprint: regional context only, deliberately excluded from
    every field- and canal-scale figure.

    COLLECTION CHOICE, ESTABLISHED BY A LIVE CHECK
    The obvious id, NASA/GRACE/MASS_GRIDS_V04/LAND, is GRACE only: it ends on
    2017-05-22 and returns nothing for any season this platform targets. The
    MASCON product carries the GRACE-FO era through to 2024 and is what any
    recent run needs. Its band is `lwe_thickness`, not the per-centre
    `lwe_thickness_csr` of the LAND product - selecting the old band name on the
    new collection would fail, and selecting the old collection returns an empty
    series that reads as "no data" rather than "wrong dataset".
    """
    try:
        col = (ee.ImageCollection("NASA/GRACE/MASS_GRIDS_V04/MASCON")
               .filterBounds(aoi).filterDate(start, end))
        if col.size().getInfo() == 0:
            return None
        v = col.mean().select("lwe_thickness").reduceRegion(
            reducer=ee.Reducer.mean(), geometry=aoi,
            scale=25000, maxPixels=1e9, bestEffort=True).getInfo()
        return v.get("lwe_thickness")
    except Exception:
        return None


# ==============================================================================
# OBSERVED-FRACTION HELPER  (feeds provenance - integrity rule 7)
# ==============================================================================

def observed_fraction(img, band: str, aoi, scale: int) -> Optional[float]:
    """
    Fraction of the AOI that carried a valid (unmasked) pixel for this band.

    A mean over an AOI that was 80% cloud is not the same measurement as a mean
    over an AOI that was fully seen, and the manager is entitled to know which.
    Computed as (count of valid pixels) / (count of pixels in an unmasked
    constant image over the same AOI).
    """
    try:
        valid = img.select(band).mask().reduceRegion(
            reducer=ee.Reducer.sum(), geometry=aoi, scale=scale,
            maxPixels=1e9, bestEffort=True).getInfo().get(band)
        allpix = ee.Image.constant(1).reduceRegion(
            reducer=ee.Reducer.sum(), geometry=aoi, scale=scale,
            maxPixels=1e9, bestEffort=True).getInfo().get("constant")
        if valid is None or not allpix:
            return None
        return round(min(1.0, valid / allpix), 3)
    except Exception:
        return None


# ==============================================================================
# ADAPTIVE THRESHOLDS  (thin ee wrappers over decision_logic)
# ==============================================================================

def robust_threshold_ee(img, band: str, aoi, scale: int = SCALE_M,
                        low_tail: bool = True) -> dict:
    """Pull the 16/50/84 percentiles from EE, then hand them to the pure,
    tested robust_threshold. The decision lives in decision_logic; this only
    fetches the numbers."""
    st = img.select(band).reduceRegion(
        reducer=ee.Reducer.percentile([16, 50, 84]),
        geometry=aoi, scale=scale, maxPixels=1e9, bestEffort=True).getInfo()
    p16, p50, p84 = (st.get(f"{band}_p16"), st.get(f"{band}_p50"),
                     st.get(f"{band}_p84"))
    thr = dl.robust_threshold(p16, p50, p84, K_SIGMA, low_tail)
    if thr is None:
        return {"available": False}
    return {"available": True, "median": round(p50, 4),
            "robust_sigma": round(dl.robust_sigma(p16, p84), 4),
            "threshold": round(thr, 4),
            "basis": "DERIVED: median +/- 2 * robust_sigma over the reference "
                     "distribution passed to this call, computed per run, not "
                     "fixed in advance"}


def otsu_threshold_ee(img, band: str, aoi, scale: int) -> dict:
    """Pull an NDVI histogram from EE and hand it to the pure Otsu splitter."""
    hist = img.select(band).reduceRegion(
        reducer=ee.Reducer.histogram(maxBuckets=64), geometry=aoi,
        scale=scale, maxPixels=1e9, bestEffort=True).getInfo().get(band)
    if not hist or "bucketMeans" not in hist:
        return {"threshold": None, "is_bimodal": False,
                "basis": "NOT AVAILABLE: no histogram"}
    return dl.otsu_threshold(hist["histogram"], hist["bucketMeans"])


# ==============================================================================
# NETWORK LAYER
# ==============================================================================

def canal_water_status(canal_geom, start: str, end: str) -> Indicator:
    """Is there water in this canal reach? Sentinel-1 VV: water is specular at
    C-band and reads as very low backscatter. Radar answers this under cloud,
    which optical cannot."""
    buf = canal_geom.buffer(CANAL_BUFFER_M)
    col = s1_collection(buf, start, end)
    n = col.size().getInfo()
    prov = Provenance(sensor="Sentinel-1 VV (IW, GRD)", date_start=start,
                      date_end=end, n_scenes=n, scale_m=SCALE_M)
    if n < MIN_S1_SCENES:
        # A bare scene count invites the wrong conclusion. From 2022 onward the
        # shortfall is the constellation, not the canal and not this query.
        season_year = int(start[:4]) if start[:4].isdigit() else None
        avail = dl.sentinel1_availability(n, season_year, MIN_S1_SCENES)
        ind = Indicator.unavailable("canal_water", avail["reason"], prov)
        ind.interpretation = avail.get("remedy", "")
        return ind
    vv = col.median()
    water = vv.lt(S1_WATER_DB)
    frac = water.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=buf,
        scale=SCALE_M, maxPixels=1e9, bestEffort=True).getInfo().get("VV")
    if frac is None:
        return Indicator.unavailable("canal_water", "no valid radar pixels", prov)
    prov.observed_fraction = observed_fraction(vv, "VV", buf, SCALE_M)
    return Indicator(
        name="canal_water", status="OK", value=round(frac, 4), unit="fraction",
        threshold=CANAL_WET_FRACTION,
        threshold_basis="ARBITRARY: hand-chosen wet-fraction cut-off",
        interpretation=(
            "Fraction of the buffered canal footprint reading as water in radar. "
            "Wet-looking is not flowing, and a dry reading can also be a narrow "
            "canal under a 10 m pixel."),
        provenance=asdict(prov))


def head_tail_equity(canal_geom, command_area, start: str, end: str,
                     n_reaches: int = N_REACHES,
                     canal_props: Optional[dict] = None) -> dict:
    """
    Split the canal into reaches head to tail and fit crop vigour against
    position by OLS, with a bootstrap CI on the gap. See decision_logic.
    fit_head_tail_slope and equity_flag for the actual decision. This function
    only samples each reach's NDVI from Earth Engine.

    Which end is the head comes from decision_logic.resolve_canal_direction, not
    from the vertex order of the file: reversing a LineString flips the sign of
    the gap, so an undeclared direction would let the engine point a manager at
    the wrong end of the canal with a confidence interval attached. Where the
    direction cannot be established the gap is withheld rather than reported
    with an unknown sign; where it is only assumed, the assumption travels with
    the number.

    It measures a difference. It does not say who caused it.
    """
    coords = canal_geom.coordinates().getInfo()
    if not coords or len(coords) < 2:
        return {"status": "NOT AVAILABLE",
                "reason": "canal geometry has too few vertices to split"}

    props = canal_props or {}
    direction = dl.resolve_canal_direction(coords,
                                           declared=props.get("vertex_order"),
                                           offtake=props.get("offtake"))
    if direction["status"] != "OK":
        return {"status": "NOT AVAILABLE",
                "reason": ("head-to-tail direction could not be established, so "
                           "the sign of the gap is unknown: "
                           + str(direction.get("reason"))),
                "direction": direction}
    if direction["reverse"]:
        coords = list(reversed(coords))

    col = s2_collection(command_area, start, end)
    n_s2 = col.size().getInfo()
    if n_s2 < MIN_S2_SCENES:
        return {"status": "INSUFFICIENT DATA",
                "reason": f"only {n_s2} Sentinel-2 scenes; {MIN_S2_SCENES} needed"}

    idx = s2_indices(col.median())
    step = max(1, len(coords) // n_reaches)
    reaches, positions, ndvis = [], [], []
    seg_index = 0
    total_segments = len(range(0, len(coords) - 1, step))
    for i in range(0, len(coords) - 1, step):
        seg = coords[i:min(i + step + 1, len(coords))]
        if len(seg) < 2:
            continue
        # Strip served by this reach. The half-width is the fallback command
        # width and is declared arbitrary; a real command-area polygon per reach
        # would replace it.
        seg_geom = ee.Geometry.LineString(seg).buffer(FALLBACK_COMMAND_HALF_WIDTH_M)
        v = idx.select("NDVI").reduceRegion(
            reducer=ee.Reducer.mean(), geometry=seg_geom,
            scale=SCALE_M * 2, maxPixels=1e9, bestEffort=True).getInfo().get("NDVI")
        pos = seg_index / max(1, total_segments - 1)   # 0=head .. 1=tail
        seg_index += 1
        if v is None:
            continue
        reaches.append({"reach": len(reaches) + 1, "position_along_canal": round(pos, 3),
                        "mean_ndvi": round(v, 4)})
        positions.append(pos)
        ndvis.append(v)

    fit = dl.fit_head_tail_slope(positions, ndvis)
    flag = dl.equity_flag(fit, EQUITY_GAP_FLAG)
    out = {
        "status": flag["status"],
        "reaches": reaches,
        "n_reaches_used": len(ndvis),
        "direction": direction,
        "provenance": asdict(Provenance(
            sensor="Sentinel-2 median NDVI", date_start=start, date_end=end,
            n_scenes=n_s2, scale_m=SCALE_M * 2,
            threshold_basis="reach strip half-width is the ARBITRARY fallback "
                            "command width; replace with real command polygons")),
        "interpretation": (
            "Mean crop vigour in the strip served by each reach, head to tail, "
            "fitted as a line so the trend uses every reach and its uncertainty "
            "is stated. A gap is a measured difference in outcome; its causes "
            "(siltation, upstream abstraction, soil, crop choice, planting date) "
            "are not separated here and nothing is attributed to anyone."),
    }
    if flag["status"] == "OK":
        out.update({
            "head_fit_ndvi": fit.head_fit, "tail_fit_ndvi": fit.tail_fit,
            "head_tail_gap": flag["gap_point_estimate"],
            "head_tail_gap_ci95": flag["gap_ci"],
            "slope": flag["slope"], "fit_r2": flag["r2"],
            "flagged": flag["flagged"],
            "gap_reliable": flag.get("gap_reliable", True),
            "flag_threshold": flag["flag_threshold"],
            "flag_rule": flag.get("flag_rule"), "flag_basis": flag.get("flag_basis"),
            "attribution_caveat": flag["attribution_caveat"],
        })
        if flag.get("reason"):
            out["reason"] = flag["reason"]
    else:
        out["reason"] = flag.get("reason")
    return out


def irrigated_extent(command_area, start: str, end: str) -> Indicator:
    """
    Fraction of the command area that actually carried an irrigated crop.

    Uses an Otsu split of the peak-season NDVI histogram - the right tool for a
    bimodal cropped/bare mixture - not median+k*sigma, which assumes a single
    hump and typically calls almost nothing cropped on a desert-fringe command
    area. The split's valley depth is reported so a weak (near-unimodal) case is
    surfaced rather than hidden behind a confident-looking number.
    """
    col = s2_collection(command_area, start, end)
    n = col.size().getInfo()
    prov = Provenance(sensor="Sentinel-2 peak-season NDVI", date_start=start,
                      date_end=end, n_scenes=n, scale_m=SCALE_M * 2)
    if n < MIN_S2_SCENES:
        return Indicator.unavailable(
            "irrigated_extent", f"only {n} Sentinel-2 scenes; {MIN_S2_SCENES} needed",
            prov)

    peak = s2_indices(col.qualityMosaic("B8"))
    otsu = otsu_threshold_ee(peak, "NDVI", command_area, SCALE_M * 2)
    if otsu.get("threshold") is None:
        return Indicator.unavailable("irrigated_extent", otsu.get("basis", "no split"),
                                     prov)

    cropped = peak.select("NDVI").gt(otsu["threshold"])
    frac = cropped.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=command_area,
        scale=SCALE_M * 2, maxPixels=1e9, bestEffort=True).getInfo().get("NDVI")
    if frac is None:
        return Indicator.unavailable("irrigated_extent", "no valid pixels", prov)

    prov.observed_fraction = observed_fraction(peak, "NDVI", command_area, SCALE_M * 2)
    prov.notes = (f"Otsu separability {otsu.get('separability')}, valley-depth "
                  f"bimodality {otsu.get('bimodality')}. "
                  + ("Clean cropped/bare split." if otsu.get("is_bimodal")
                     else "WEAK SPLIT: histogram is near-unimodal, so this extent "
                          "figure is unreliable and should be read with caution."))
    return Indicator(
        name="irrigated_extent", status="OK", value=round(frac, 4),
        unit="fraction", threshold=otsu["threshold"], threshold_basis=otsu["basis"],
        interpretation=(
            "Share of the command area above the Otsu cropped/bare split. "
            "Compare against the planned command area to see how much of the "
            "scheme was actually cropped. Trust it only when the bimodality in "
            "the provenance is high."),
        provenance=asdict(prov))


# ==============================================================================
# FIELD LAYER  (with the rainfall context that makes stress interpretable)
# ==============================================================================

def field_condition(field_geom, start: str, end: str, reference_geom=None) -> dict:
    """
    Field-scale condition with the cause-separating context attached. Never
    reports stress on its own: a stressed field after a dry fortnight is a
    drought observation; a stressed field despite rain points at the network.
    Reporting the first as a network alert wastes a manager's day and erodes
    trust in every later alert.

    `reference_geom` is the command area / neighbourhood the field's stress
    threshold is derived from. It MUST be a wider population than the field
    itself: a field's mean is never below its OWN (median - 2 sigma), so a
    threshold derived from the field alone would make stress impossible to
    detect. When no reference is supplied the vigour/moisture VALUES are still
    reported, but no relative stress threshold and no stress verdict are - an
    honest "not available" rather than a threshold that can never fire.
    """
    out: dict = {"indicators": {}, "context": {}}
    ref = reference_geom if reference_geom is not None else None

    col = s2_collection(field_geom, start, end)
    n_s2 = col.size().getInfo()
    if n_s2 < MIN_S2_SCENES:
        out["indicators"]["vigour"] = asdict(Indicator.unavailable(
            "vigour", f"only {n_s2} Sentinel-2 scenes",
            Provenance(sensor="Sentinel-2", date_start=start, date_end=end,
                       n_scenes=n_s2)))
        return out

    idx = s2_indices(col.median())
    # Thresholds come from the reference distribution, computed once, NOT from
    # the field itself. If no reference geometry is given, there is no relative
    # threshold and the stress verdict is withheld.
    ref_idx = s2_indices(s2_collection(ref, start, end).median()) if ref is not None else None
    for band, name, interp in (
        ("NDVI", "vigour", "Mean crop vigour over the window."),
        ("NDMI", "canopy_moisture",
         "Canopy water content. Falls before visible vigour does, so it is the "
         "earlier warning of the two."),
    ):
        v = idx.select(band).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=field_geom,
            scale=SCALE_M, maxPixels=1e9, bestEffort=True).getInfo().get(band)
        prov = Provenance(sensor="Sentinel-2 median", date_start=start,
                          date_end=end, n_scenes=n_s2, scale_m=SCALE_M,
                          observed_fraction=observed_fraction(idx, band, field_geom, SCALE_M))
        if v is None:
            out["indicators"][name] = asdict(Indicator.unavailable(name, "no pixels", prov))
        else:
            if ref_idx is not None:
                thr = robust_threshold_ee(ref_idx, band, ref, low_tail=True)
            else:
                thr = {"available": False,
                       "basis": "NOT AVAILABLE: no reference area, so no relative "
                                "threshold (a field vs itself can never flag)"}
            prov.threshold_basis = thr.get("basis")
            out["indicators"][name] = asdict(Indicator(
                name=name, status="OK", value=round(v, 4),
                threshold=thr.get("threshold"), threshold_basis=thr.get("basis"),
                interpretation=interp, provenance=asdict(prov)))

    # Thermal - a direct physical measure, not a proxy
    tcol = thermal_collection(field_geom, start, end)
    n_t = tcol.size().getInfo()
    tprov = Provenance(sensor="Landsat 8/9 ST_B10", date_start=start, date_end=end,
                       n_scenes=n_t, scale_m=SCALE_COARSE_M)
    if n_t == 0:
        out["indicators"]["thermal_stress"] = asdict(Indicator.unavailable(
            "thermal_stress", "no cloud-free Landsat thermal scenes in window", tprov))
    else:
        v = tcol.median().reduceRegion(
            reducer=ee.Reducer.mean(), geometry=field_geom,
            scale=SCALE_COARSE_M, maxPixels=1e9, bestEffort=True).getInfo().get("LST")
        if v is None:
            out["indicators"]["thermal_stress"] = asdict(Indicator.unavailable(
                "thermal_stress", "no valid thermal pixels", tprov))
        else:
            out["indicators"]["thermal_stress"] = asdict(Indicator(
                name="thermal_stress", status="OK", value=round(v, 2), unit="degC",
                interpretation=(
                    "Land surface temperature. A transpiring crop is cooler than "
                    "a stressed one, so a warm field relative to its neighbours "
                    "is a direct water-stress signal. 100 m: large fields only."),
                provenance=asdict(tprov)))

    # The context that decides whether stress means anything (integrity rule 3)
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    rain_start = (end_dt - timedelta(days=RAIN_WINDOW_DAYS)).strftime("%Y-%m-%d")
    rain = rainfall_mm(field_geom, rain_start, end)
    out["context"]["rainfall_mm_last_14d"] = round(rain, 1) if rain is not None else None
    out["context"]["rainfall_source"] = "CHIRPS daily"
    out["context"]["rain_floor_mm"] = RAIN_LOW_MM
    out["context"]["rain_floor_basis"] = "ARBITRARY"

    vig = out["indicators"].get("vigour", {})
    sr = dl.stress_reading(vig.get("value"), vig.get("threshold"),
                           rain, RAIN_LOW_MM)
    out["context"]["reading"] = sr.get("reading") or sr.get("reason")
    out["context"]["reading_status"] = sr["status"]
    return out


# ==============================================================================
# COMMAND-AREA RESOLUTION  (defect 1 fix)
# ==============================================================================

def resolve_command_area(canal_feature: dict, canal_geom,
                         command_fc: Optional[dict]) -> tuple:
    """
    Return (ee.Geometry command_area, provenance_dict).

    Priority:
      1. A command-area polygon whose 'canal' (or 'canal_name'/'name') property
         matches this canal's name -> use it, provenance says REAL/matched.
      2. Any command polygon that intersects the canal buffer -> union of them,
         provenance says REAL/spatial.
      3. No polygons -> the arbitrary buffer, provenance says SYNTHETIC.

    The point is that when real geometry exists it is used and recorded as real,
    and when it does not the synthetic fallback is recorded as synthetic - never
    silently swapped in behind a figure that looks the same either way.
    """
    canal_name = canal_feature.get("properties", {}).get("name", "")
    if command_fc and command_fc.get("features"):
        matched = []
        for cf in command_fc["features"]:
            props = cf.get("properties", {})
            ref = (props.get("canal") or props.get("canal_name")
                   or props.get("name") or "")
            if canal_name and str(ref).strip().lower() == canal_name.strip().lower():
                matched.append(ee.Feature(ee.Geometry(cf["geometry"])))
        if matched:
            geom = ee.FeatureCollection(matched).geometry()
            # Area comes from the GeoJSON, not from a server round trip. It is
            # needed for the consumption half of water-use efficiency, and
            # asking the operator to supply a hectare figure they would have to
            # look up is a worse answer than computing it from the polygon they
            # already gave us.
            ha = sum(dl.geojson_area_m2(cf.get("geometry")) or 0.0
                     for cf in command_fc["features"]
                     if str((cf.get("properties", {}) or {}).get("canal")
                            or (cf.get("properties", {}) or {}).get("canal_name")
                            or (cf.get("properties", {}) or {}).get("name") or "")
                     .strip().lower() == canal_name.strip().lower()) / 10000.0
            return geom, {"command_area_source": "REAL: matched by name property",
                          "matched_polygons": len(matched),
                          "command_area_ha": round(ha, 1),
                          "area_basis": "computed from the supplied polygon"}

        # spatial fallback: polygons intersecting the canal buffer
        buf = canal_geom.buffer(FALLBACK_COMMAND_HALF_WIDTH_M)
        allfc = ee.FeatureCollection([
            ee.Feature(ee.Geometry(cf["geometry"])) for cf in command_fc["features"]])
        inter = allfc.filterBounds(buf)
        if inter.size().getInfo() > 0:
            return inter.geometry(), {
                "command_area_source": "REAL: polygons intersecting the canal buffer",
                "matched_polygons": inter.size().getInfo()}

    return canal_geom.buffer(FALLBACK_COMMAND_HALF_WIDTH_M), {
        "command_area_source": "SYNTHETIC: arbitrary buffer, no command-area "
                               "polygon supplied for this canal",
        "buffer_half_width_m": FALLBACK_COMMAND_HALF_WIDTH_M}


def resolve_field_reference(field_feature: dict,
                            command_fc: Optional[dict]) -> tuple:
    """
    Return (ee.Geometry reference or None, provenance dict) for a field's stress
    threshold.

    Priority, widest-defensible-first:
      1. The command-area polygon the field's centroid falls inside.
      2. The union of every command polygon supplied.
      3. Nothing -> None, and field_condition withholds the verdict.

    Each candidate must pass decision_logic.reference_adequate. A command area
    barely bigger than the field is rejected and the next candidate tried,
    because a threshold the field itself sets can never flag the field. When
    nothing passes, the indicator VALUES are still reported - only the verdict is
    withheld. That is the difference between "we did not measure this" and "we
    measured it and it is fine", and the two must never look alike.
    """
    geom_json = field_feature.get("geometry")
    field_area = dl.geojson_area_m2(geom_json)
    centroid = dl.geojson_centroid(geom_json)

    if not command_fc or not command_fc.get("features"):
        return None, {"reference_source": "NOT AVAILABLE: no command-area "
                                          "polygons supplied, so no population "
                                          "to derive a threshold from",
                      "field_area_m2": round(field_area) if field_area else None,
                      "verdict_withheld": True}

    feats = command_fc["features"]
    rejected = []

    # 1. the containing command area
    if centroid:
        for cf in feats:
            if dl.point_in_geometry(centroid, cf.get("geometry")):
                area = dl.geojson_area_m2(cf.get("geometry"))
                adq = dl.reference_adequate(field_area, area)
                if adq["ok"]:
                    return ee.Geometry(cf["geometry"]), {
                        "reference_source": "REAL: command area containing the field",
                        "reference_name": cf.get("properties", {}).get("canal"),
                        "field_area_m2": round(field_area),
                        "reference_area_m2": round(area),
                        "area_ratio": adq["ratio"],
                        "area_ratio_basis": adq["basis"],
                        "verdict_withheld": False}
                rejected.append(adq["reason"])
                break

    # 2. the union of all command areas
    total = sum(dl.geojson_area_m2(cf.get("geometry")) or 0.0 for cf in feats)
    adq = dl.reference_adequate(field_area, total)
    if adq["ok"]:
        union = ee.FeatureCollection([
            ee.Feature(ee.Geometry(cf["geometry"])) for cf in feats]).geometry()
        return union, {
            "reference_source": "REAL: union of all supplied command areas "
                                "(no single containing area was adequate)"
                                if rejected else
                                "REAL: union of all supplied command areas "
                                "(field centroid is in none of them)",
            "field_area_m2": round(field_area) if field_area else None,
            "reference_area_m2": round(total),
            "area_ratio": adq["ratio"],
            "area_ratio_basis": adq["basis"],
            "rejected_candidates": rejected or None,
            "verdict_withheld": False}

    rejected.append(adq["reason"])
    return None, {"reference_source": "NOT AVAILABLE: no candidate reference was "
                                      "wide enough relative to the field",
                  "field_area_m2": round(field_area) if field_area else None,
                  "rejected_candidates": rejected,
                  "verdict_withheld": True}


# ==============================================================================
# ASSEMBLY
# ==============================================================================

def analyse(canal_fc: dict, command_fc: Optional[dict], season: int,
            out_json: str, crop: str = "default",
            nutrition_climate: bool = True,
            field_fc: Optional[dict] = None,
            rangeland_fc: Optional[dict] = None) -> dict:
    proj = init_ee()
    start, end = season_window(season)

    print("=" * 72)
    print("Sudan Irrigation & Agriculture Monitor")
    print("=" * 72)
    print(f"Season  : {start} to {end}")
    print(f"Project : {proj}")
    feats = canal_fc.get("features", [])
    field_feats = (field_fc or {}).get("features", [])
    print(f"Canals  : {len(feats)}")
    print(f"Command : {'supplied' if command_fc else 'NONE - synthetic buffers'}")
    print(f"Fields  : {len(field_feats) if field_feats else 'NONE - field layer skipped'}")

    # nutrition + climate live in the sibling module; import lazily so the
    # network/field engine still runs if that module is absent.
    ncg = None
    if nutrition_climate:
        try:
            import nutrition_climate_ground as ncg  # noqa
        except Exception as e:
            print(f"  (nutrition/climate module unavailable: {e})")
            ncg = None

    # agronomy (crop water requirement, forecast, yield gate) and rangeland are
    # optional in the same way: a missing module degrades the report, it does
    # not stop the run, and the absence is recorded rather than passed over.
    try:
        import agronomy as agro
    except Exception as e:
        print(f"  (agronomy module unavailable: {e})")
        agro = None
    try:
        import rangeland as rng
    except Exception as e:
        print(f"  (rangeland module unavailable: {e})")
        rng = None
    try:
        import network as netw
    except Exception as e:
        print(f"  (network module unavailable: {e})")
        netw = None

    results = {
        "tool": "Sudan Irrigation & Agriculture Monitor",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "gee_project": proj,
        "season": {"start": start, "end": end},
        "crop": crop,
        "command_geometry_supplied": bool(command_fc),
        "sensors": {
            "Sentinel-2": "10 m optical - vigour, moisture, irrigated extent",
            "Sentinel-2 red-edge": "B5-B7 - chlorophyll indices for nutrition",
            "Sentinel-1": "C-band radar - canal water, cloud-penetrating",
            "Landsat 8/9 thermal": "100 m LST - direct water-stress measure",
            "CHIRPS": "daily rainfall - separates drought from network failure",
            "ERA5-Land": "temperature - growing-degree days, heat stress",
            "MODIS MOD16": "500 m actual ET - command-scale water consumption",
            "GRACE-FO": "~300 km storage anomaly - REGIONAL CONTEXT ONLY",
        },
        "canals": [],
        "fields": [],
        "rangeland": [],
        "field_geometry_supplied": bool(field_feats),
        "regional_context": {},
        "limitations": [
            "Every figure describes measured surface condition. None of it is "
            "attributed to any office, operator or decision.",
            "A head-to-tail gap has many possible causes - siltation, upstream "
            "abstraction, soil, crop choice, planting date. This engine "
            "separates none of them.",
            "The head-to-tail gap is signed, and nothing in a LineString "
            "records which end the water enters. Direction comes from a "
            "'vertex_order' property or an 'offtake' coordinate; with neither, "
            "vertex order is ASSUMED and the gap's sign is unverified.",
            "Radar shows standing water, not flow. A canal full and static "
            "reads identically to one carrying its design discharge, so no "
            "figure here describes movement of water.",
            "Continuity says where standing water was last detected. An "
            "unobserved reach is not a dry reach and never counts as one.",
            "Siltation output is a list of reaches worth inspecting. Sediment "
            "depth is not observable from orbit and none is estimated.",
            "Network water-use efficiency needs a measured release volume from "
            "the scheme authority. Without it the consumption is reported and "
            "the ratio is withheld; a design discharge is never substituted.",
            "Canals narrower than about 20 m are below reliable detection at "
            "10 m radar resolution.",
            "Thermal is 100 m and 16-day: a large-field and canal-command "
            "measure, not a smallholding one.",
            "GRACE-FO cannot see a canal or a field; reported as regional "
            "context only.",
            "An absolute nitrogen figure is reported ONLY where a calibrated "
            "model exists for the crop and its RMSE is within limit; otherwise "
            "only a relative condition is given. Yield is refused on the same "
            "terms and for the same reason.",
            "Crop water requirement is what the crop NEEDED. Nothing in this "
            "engine measures what any field RECEIVED, and the two must never "
            "be read as the same number.",
            "ET0 is computed from ERA5-Land at about 11 km. It is a command- "
            "and scheme-scale figure, not a field-scale one.",
            "The 7-day outlook is NOAA GFS at about 28 km: the weather over "
            "the scheme, not over a field. No alert is raised from it.",
            "Rangeland and corridor figures describe vegetation and surface "
            "water only. They say nothing about who may use land, and are not "
            "evidence of any claim by any party.",
        ],
    }

    all_geom = ee.FeatureCollection([
        ee.Feature(ee.Geometry(f["geometry"])) for f in feats]).geometry()

    # Scheme-wide red-edge percentiles for the nutrition relative-condition band.
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
            print(f"  (scheme nutrition stats unavailable: {e})")

    for i, f in enumerate(feats, 1):
        name = f.get("properties", {}).get("name", f"canal_{i}")
        geom = ee.Geometry(f["geometry"])
        print(f"\n[{i}/{len(feats)}] {name}")

        command, cmd_prov = resolve_command_area(f, geom, command_fc)
        print(f"  command area     : {cmd_prov['command_area_source'].split(':')[0]}")

        water = canal_water_status(geom, start, end)
        print(f"  canal water      : {water.status}"
              + (f"  {water.value}" if water.value is not None else f"  ({water.reason})"))

        equity = head_tail_equity(geom, command, start, end,
                                  canal_props=f.get("properties", {}))
        if equity.get("direction") and not equity["direction"].get("verified", True):
            print("  head/tail direction: ASSUMED from vertex order - "
                  "supply 'vertex_order' or 'offtake' to verify the sign")
        if equity["status"] == "OK":
            print(f"  head/tail gap    : {equity['head_tail_gap']} "
                  f"(CI {equity['head_tail_gap_ci95']})"
                  + ("  FLAGGED" if equity["flagged"] else ""))
        else:
            print(f"  head/tail gap    : {equity['status']} ({equity.get('reason','')})")

        extent = irrigated_extent(command, start, end)
        print(f"  irrigated extent : {extent.status}"
              + (f"  {extent.value}" if extent.value is not None else ""))

        et = evapotranspiration_mm(command, start, end)
        rain = rainfall_mm(command, start, end)

        # WHERE the water stopped, not just whether it was there. Uses the same
        # resolved direction as the equity fit, so a break is reported at the
        # correct end of the canal or not at all.
        continuity = None
        if netw is not None:
            direction = (equity.get("direction") or {}) if isinstance(equity, dict) else {}
            continuity = netw.canal_continuity(
                geom, start, end,
                reverse=bool(direction.get("reverse")),
                canal_width_m=f.get("properties", {}).get("width_m"))
            if continuity.get("status") == "OK":
                phrase = {
                    "STOPS": (f"water not detected beyond reach "
                              f"{continuity.get('water_reaches_to')} of "
                              f"{continuity.get('n_reaches')}"),
                    "CONTINUOUS": "no break detected",
                    "NO WATER DETECTED": "no standing water in any reach",
                    "INTERMITTENT": ("intermittent - wet and dry interleaved, "
                                     "so there is no single stopping point"),
                }.get(continuity.get("pattern"), continuity.get("pattern", "?"))
                print(f"  continuity       : {phrase}"
                      + f"  ({continuity['wet_reaches']} wet / "
                        f"{continuity['dry_reaches']} dry / "
                        f"{continuity['unobserved_reaches']} unseen)")
            else:
                print(f"  continuity       : {continuity.get('status')} "
                      f"({continuity.get('reason', '')})")

        canal_record = {
            "name": name,
            "command_area_provenance": cmd_prov,
            "canal_water": asdict(water),
            "head_tail_equity": equity,
            "irrigated_extent": asdict(extent),
            "seasonal_et_mm": round(et, 1) if et is not None else None,
            "seasonal_rainfall_mm": round(rain, 1) if rain is not None else None,
        }
        if continuity is not None:
            canal_record["continuity"] = continuity

        # Efficiency, which in practice means the refusal. The release volume
        # comes from the scheme authority's records, not from a satellite, and
        # `water_released_m3` on the canal feature is where it goes when it
        # exists. Without it the consumption half is reported and the ratio is
        # withheld rather than approximated from a design discharge.
        if netw is not None:
            # Area from the real command polygon where one was matched; the
            # canal's own property only as an override. The synthetic buffer
            # deliberately supplies none - an efficiency figure over an
            # arbitrary 1.5 km strip would be arithmetic on a made-up area.
            area_ha = (f.get("properties", {}).get("command_area_ha")
                       or cmd_prov.get("command_area_ha"))
            canal_record["water_use_efficiency"] = netw.water_use_efficiency(
                et_consumed_mm=et,
                command_area_ha=area_ha,
                water_released_m3=f.get("properties", {}).get("water_released_m3"))

        # Command-scale crop water requirement. Paired with the canal-water and
        # rainfall figures above, this is the pair of numbers the whole platform
        # is built around: what the command needed, and whether the canal that
        # was supposed to supply it had water in it. The engine reports both and
        # joins neither into a causal claim.
        if agro is not None:
            try:
                cmd_ndvi = s2_indices(s2_collection(command, start, end).median()) \
                    .select("NDVI").reduceRegion(
                        reducer=ee.Reducer.mean(), geometry=command,
                        scale=SCALE_M * 2, maxPixels=1e9,
                        bestEffort=True).getInfo().get("NDVI")
                canal_record["water_requirement"] = agro.crop_water_requirement(
                    command, start, end, cmd_ndvi)
            except Exception as e:
                canal_record["water_requirement"] = {"status": "NOT AVAILABLE",
                                                     "reason": str(e)}

        if ncg is not None:
            try:
                canal_record["climate"] = ncg.climate_context(command, start, end, crop)
            except Exception as e:
                canal_record["climate"] = {"status": "NOT AVAILABLE", "reason": str(e)}
            try:
                ncol = s2_collection(command, start, end)
                if ncol.size().getInfo() >= MIN_S2_SCENES:
                    cidx = ncg.chlorophyll_indices(ncol.median())
                    fvals = cidx.reduceRegion(
                        reducer=ee.Reducer.mean(), geometry=command,
                        scale=SCALE_M * 2, maxPixels=1e9, bestEffort=True).getInfo()
                    nres = ncg.nutrition_status(fvals, scheme_stats, crop)
                    canal_record["nutrition"] = ncg.asdict_nutrition(nres)
                else:
                    canal_record["nutrition"] = {"status": "NOT AVAILABLE",
                                                 "reason": "too few S2 scenes"}
            except Exception as e:
                canal_record["nutrition"] = {"status": "NOT AVAILABLE", "reason": str(e)}

        results["canals"].append(canal_record)

    # --- FIELD LAYER ----------------------------------------------------------
    # Runs only when field polygons are supplied. Without them there is no field
    # to report on, and inventing one from the canal buffer would be exactly the
    # silent substitution the command-area fallback was fixed to stop doing.
    for j, ff in enumerate(field_feats, 1):
        fname = ff.get("properties", {}).get("name", f"field_{j}")
        fgeom = ee.Geometry(ff["geometry"])
        print(f"\n[field {j}/{len(field_feats)}] {fname}")

        ref_geom, ref_prov = resolve_field_reference(ff, command_fc)
        print(f"  reference        : {ref_prov['reference_source'].split(':')[0]}"
              + (f"  (x{ref_prov['area_ratio']})" if ref_prov.get("area_ratio") else ""))

        cond = field_condition(fgeom, start, end, reference_geom=ref_geom)
        print(f"  reading          : {cond['context'].get('reading')}")

        field_record = {
            "name": fname,
            "properties": ff.get("properties", {}),
            "reference_provenance": ref_prov,
            "condition": cond,
        }

        if agro is not None:
            try:
                vig = (cond.get("indicators", {}).get("vigour") or {})
                field_record["water_requirement"] = agro.crop_water_requirement(
                    fgeom, start, end,
                    vig.get("value") if vig.get("status") == "OK" else None)
                field_record["yield_estimate"] = agro.yield_estimate(
                    vig.get("value") if vig.get("status") == "OK" else None, crop)
            except Exception as e:
                field_record["water_requirement"] = {"status": "NOT AVAILABLE",
                                                     "reason": str(e)}

        if ncg is not None:
            try:
                fcol = s2_collection(fgeom, start, end)
                if fcol.size().getInfo() >= MIN_S2_SCENES:
                    cidx = ncg.chlorophyll_indices(fcol.median())
                    fvals = cidx.reduceRegion(
                        reducer=ee.Reducer.mean(), geometry=fgeom,
                        scale=SCALE_M, maxPixels=1e9, bestEffort=True).getInfo()
                    # A reference STRIP is the farmer's over-fertilised strip and
                    # is a different thing from the stress reference area: it is
                    # supplied per field, by name, or the ladder stays at Level 1.
                    strip = ff.get("properties", {}).get("reference_strip")
                    strip_vals = None
                    if strip:
                        sidx = ncg.chlorophyll_indices(
                            s2_collection(ee.Geometry(strip), start, end).median())
                        strip_vals = sidx.reduceRegion(
                            reducer=ee.Reducer.mean(),
                            geometry=ee.Geometry(strip),
                            scale=SCALE_M, maxPixels=1e9,
                            bestEffort=True).getInfo()
                    nres = ncg.nutrition_status(fvals, scheme_stats, crop,
                                                reference_indices=strip_vals)
                    field_record["nutrition"] = ncg.asdict_nutrition(nres)
                else:
                    field_record["nutrition"] = {"status": "NOT AVAILABLE",
                                                 "reason": "too few S2 scenes"}
            except Exception as e:
                field_record["nutrition"] = {"status": "NOT AVAILABLE", "reason": str(e)}

        results["fields"].append(field_record)

    # --- RANGELAND LAYER ------------------------------------------------------
    # Separate from the irrigation layers on purpose. It answers a different
    # question for a different user, and it carries a conflict-sensitivity note
    # the irrigation layers do not need.
    range_feats = (rangeland_fc or {}).get("features", [])
    if range_feats and rng is not None:
        print(f"\nRangeland areas: {len(range_feats)}")
        for ra in range_feats:
            rec = rng.analyse_rangeland(ra, start, end)
            status = rec.get("status")
            print(f"  {ra.get('properties', {}).get('name', '?')}: {status}")
            if status == "REFUSED":
                print(f"    {rec.get('reason')}")
            results["rangeland"].append(rec)
    elif range_feats:
        results["rangeland"] = [{"status": "NOT AVAILABLE",
                                 "reason": "rangeland module unavailable"}]

    # A short-range outlook for the scheme as a whole. Deliberately scheme-level:
    # GFS is a ~28 km model and a per-field forecast would be false precision.
    if agro is not None:
        results["forecast"] = agro.forecast_7day(all_geom)

    gw = groundwater_context(all_geom, start, end)
    results["regional_context"] = {
        "grace_lwe_cm": round(gw, 2) if gw is not None else None,
        "note": ("GRACE-FO footprint is roughly 300 km. This describes the "
                 "region and says nothing about any canal or field."),
    }

    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)

    print("\n" + "=" * 72)
    print(f"Written to {out_json}")
    print("These are screening measurements over irrigated land. They describe "
          "condition and difference; they attribute nothing to anyone.")
    return results
