"""
Sudan Irrigation & Agriculture Monitor - crop nutrition, climate, and ground
observation layers.

This module adds three things the network/field engine cannot do on its own:

  NUTRITION  Red-edge chlorophyll indicators, a reference-strip sufficiency
             method, and a calibration store that upgrades a relative index into
             a real nitrogen number once field measurements exist.
  CLIMATE    Growing-degree days, heat stress, dry spells, and how this season
             compares with the site's own history.
  GROUND     Phone and drone photographs tied to a field, a date and a GPS
             position, forming the ground truth everything else is checked
             against.

WHY THE NITROGEN LAYER IS BUILT THE WAY IT IS
---------------------------------------------
Nitrogen has no direct spectral signature in the range Sentinel-2 measures. What
satellites see is chlorophyll, and the chain nitrogen -> chlorophyll ->
reflectance -> index is broken in three places: water stress, salinity, disease,
sulphur and iron deficiency and heat all lower chlorophyll; the
nitrogen-to-chlorophyll relationship differs by crop, variety and stage; and
nitrogen concentration falls naturally as biomass accumulates. So this module
reports a RELATIVE chlorophyll condition by default, and an absolute nitrogen
figure ONLY where a calibrated model exists for the crop, quoted with its error.

WHAT CHANGED FROM THE FIRST DRAFT
---------------------------------
- dry_spells() and season_vs_history() no longer call getInfo() once per day /
  per year. The first draft made up to ~400 blocking round-trips to Earth Engine
  for one canal's dry-spell figure - unusable at scheme scale. Both now pull
  their whole series in a single aggregate_array() call and hand it to the pure,
  tested functions in decision_logic.
- The nitrogen ladder's decisions (relative band, sufficiency reading,
  calibration gate) are delegated to decision_logic so they are unit-tested.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field as dc_field, asdict
from datetime import datetime, timezone
from typing import Optional

import decision_logic as dl

try:
    import ee
except ImportError:
    ee = None


# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Reference-strip sufficiency cut-offs. ARBITRARY: conventional starting values,
# not derived from Sudanese yield-response data. Replace when local data exists.
SUFFICIENCY_DEFICIENT = 0.90
SUFFICIENCY_MARGINAL = 0.95

MIN_CALIBRATION_POINTS = 30     # below this, no absolute nitrogen is reported
MAX_ACCEPTABLE_RMSE_PCT = 0.6   # leaf N %, above which the model is too weak

# Heat-stress thresholds by crop (deg C). Typical published figures, NOT
# measurements from Sudanese trials. ARBITRARY until local trial data replaces.
HEAT_STRESS_C = {"wheat": 32.0, "sorghum": 38.0, "cotton": 35.0,
                 "groundnut": 34.0, "default": 35.0}

# Base temperature for growing-degree-day accumulation (deg C).
GDD_BASE_C = {"wheat": 4.0, "sorghum": 10.0, "cotton": 15.5,
              "groundnut": 10.0, "default": 10.0}

# Native pixel sizes of the coarse datasets. NOT arbitrary - these are the
# published resolutions, and they set the smallest region that can be
# reduced without returning a silent None.
ERA5_NATIVE_M = 11000
CHIRPS_NATIVE_M = 5500

DRY_SPELL_DAYS = 10             # consecutive days below the rain floor (ARBITRARY)
DRY_SPELL_RAIN_MM = 1.0         # rain floor (ARBITRARY)


# ==============================================================================
# CROP NUTRITION - red-edge indicators
# ==============================================================================

def chlorophyll_indices(img):
    """
    Red-edge indices, which is where nitrogen work actually happens.

    CIre  (B7/B5)-1     strongest single performer for canopy nitrogen; keeps
                        responding where NDVI has saturated
    MTCI  (B6-B5)/(B5-B4)   designed for chlorophyll content
    NDRE  (B8-B5)/(B8+B5)   NDVI-like but with the red edge, so useful at high
                        biomass
    S2REP red-edge position (nm), the most physically grounded of the four
    """
    b = img.divide(10000)
    b4, b5, b6, b7, b8 = (b.select("B4"), b.select("B5"), b.select("B6"),
                          b.select("B7"), b.select("B8"))
    cire = b7.divide(b5).subtract(1).rename("CIre")
    mtci = b6.subtract(b5).divide(b5.subtract(b4)).rename("MTCI")
    ndre = b8.subtract(b5).divide(b8.add(b5)).rename("NDRE")
    s2rep = b.expression(
        "705 + 35 * ((((R4 + R7) / 2) - R5) / (R6 - R5))",
        {"R4": b4, "R5": b5, "R6": b6, "R7": b7}).rename("S2REP")
    return ee.Image.cat([cire, mtci, ndre, s2rep])


@dataclass
class NutritionResult:
    status: str                                  # OK | NOT AVAILABLE
    chlorophyll_indices: dict = dc_field(default_factory=dict)
    relative_condition: Optional[str] = None
    percentile_in_scheme: Optional[float] = None
    sufficiency_index: Optional[float] = None
    sufficiency_reading: Optional[str] = None
    nitrogen_pct: Optional[float] = None
    nitrogen_confidence: Optional[dict] = None
    claim_level: str = "relative"                # relative | sufficiency | calibrated
    reason: Optional[str] = None
    caveat: str = (
        "Chlorophyll indices respond to nitrogen, and also to water stress, "
        "salinity, disease, sulphur and iron deficiency and heat. A low value "
        "identifies a struggling crop; it does not by itself identify the cause.")


def asdict_nutrition(res: NutritionResult) -> dict:
    """Single place that serialises a NutritionResult, so the engine does not
    reach into the dataclass internals."""
    return asdict(res)


# ==============================================================================
# CALIBRATION STORE - what turns an index into a measurement
# ==============================================================================

class CalibrationStore:
    """
    Field measurements that upgrade a relative index into an absolute number.

    SPAD  handheld chlorophyll meter, seconds per reading (~USD 400-800 device)
    LAB   leaf tissue nitrogen from a laboratory, the reference standard
          (~USD 10-30 per sample)

    A linear model is fitted per crop. Below MIN_CALIBRATION_POINTS or above
    MAX_ACCEPTABLE_RMSE_PCT no absolute nitrogen figure is produced - the caller
    falls back to the relative statement. The gate itself is in
    decision_logic.calibration_gate and is unit-tested.
    """

    def __init__(self, path: str = "calibration.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crop TEXT NOT NULL, observed_at TEXT NOT NULL,
                lat REAL NOT NULL, lon REAL NOT NULL, growth_stage TEXT,
                cire REAL, mtci REAL, ndre REAL, s2rep REAL,
                spad REAL, leaf_n_pct REAL,
                method TEXT NOT NULL, operator TEXT, notes TEXT
            )""")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS model (
                crop TEXT PRIMARY KEY, slope REAL, intercept REAL,
                r2 REAL, rmse REAL, n_points INTEGER,
                predictor TEXT, fitted_at TEXT
            )""")
        self.conn.commit()

    def add_point(self, crop: str, lat: float, lon: float, indices: dict,
                  leaf_n_pct: Optional[float] = None, spad: Optional[float] = None,
                  growth_stage: str = "", operator: str = "", notes: str = "") -> int:
        if leaf_n_pct is None and spad is None:
            raise ValueError(
                "a calibration point needs either a laboratory leaf-nitrogen "
                "value or a SPAD reading; an index on its own calibrates nothing")
        method = "LAB" if leaf_n_pct is not None else "SPAD"
        cur = self.conn.execute(
            "INSERT INTO calibration (crop, observed_at, lat, lon, growth_stage,"
            " cire, mtci, ndre, s2rep, spad, leaf_n_pct, method, operator, notes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (crop, datetime.now(timezone.utc).isoformat(), lat, lon, growth_stage,
             indices.get("CIre"), indices.get("MTCI"), indices.get("NDRE"),
             indices.get("S2REP"), spad, leaf_n_pct, method, operator, notes))
        self.conn.commit()
        return cur.lastrowid

    def fit(self, crop: str, predictor: str = "cire") -> dict:
        """
        Fit leaf nitrogen against one index by ordinary least squares.

        Deliberately simple: with a few dozen points a linear fit is the most
        that is defensible; a complex model on thirty samples fits noise and
        reports a flattering error. The min-points / RMSE decision is enforced
        here and mirrored by decision_logic.calibration_gate at prediction time.
        """
        rows = self.conn.execute(
            f"SELECT {predictor}, leaf_n_pct FROM calibration "
            "WHERE crop = ? AND leaf_n_pct IS NOT NULL AND "
            f"{predictor} IS NOT NULL", (crop,)).fetchall()
        n = len(rows)
        gate = dl.calibration_gate(n, rmse=0.0, min_points=MIN_CALIBRATION_POINTS,
                                   max_rmse=MAX_ACCEPTABLE_RMSE_PCT)
        if n < MIN_CALIBRATION_POINTS:
            return {"fitted": False, "n_points": n, "reason": gate["reason"]}

        xs = [r[0] for r in rows]
        ys = [r[1] for r in rows]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx == 0:
            return {"fitted": False, "n_points": n,
                    "reason": "the predictor has no variance in these points"}
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        slope = sxy / sxx
        intercept = my - slope * mx
        preds = [slope * x + intercept for x in xs]
        ss_res = sum((y - p) ** 2 for y, p in zip(ys, preds))
        ss_tot = sum((y - my) ** 2 for y in ys)
        r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
        # Residual standard error uses n - 2 (two fitted parameters), NOT n.
        # Dividing by n understates the error by ~sqrt(n/(n-2)) and could let a
        # borderline model slip past the RMSE quote-gate it should fail. n is
        # always >= MIN_CALIBRATION_POINTS (30) here, so n - 2 is safe.
        rmse = math.sqrt(ss_res / (n - 2))

        self.conn.execute(
            "INSERT OR REPLACE INTO model (crop, slope, intercept, r2, rmse,"
            " n_points, predictor, fitted_at) VALUES (?,?,?,?,?,?,?,?)",
            (crop, slope, intercept, r2, rmse, n, predictor,
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()
        return {"fitted": True, "crop": crop, "predictor": predictor,
                "slope": round(slope, 4), "intercept": round(intercept, 4),
                "r2": round(r2, 3), "rmse": round(rmse, 3), "n_points": n,
                "usable": rmse <= MAX_ACCEPTABLE_RMSE_PCT,
                "note": "Quote the RMSE with every predicted value. A number "
                        "without its error is not a measurement."}

    def predict(self, crop: str, indices: dict) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT slope, intercept, r2, rmse, n_points, predictor "
            "FROM model WHERE crop = ?", (crop,)).fetchone()
        if not row:
            return None
        slope, intercept, r2, rmse, n, predictor = row
        gate = dl.calibration_gate(n, rmse, MIN_CALIBRATION_POINTS,
                                   MAX_ACCEPTABLE_RMSE_PCT)
        if not gate["may_quote"]:
            return {"available": False, "reason": gate["reason"]}
        x = None
        for key, val in indices.items():
            if key.lower() == predictor.lower():
                x = val
                break
        if x is None:
            return {"available": False,
                    "reason": f"index {predictor} missing for this field"}
        return {"available": True,
                "nitrogen_pct": round(slope * x + intercept, 2),
                "confidence": {"r2": r2, "rmse_pct": rmse, "n_points": n,
                               "predictor": predictor, "crop": crop}}

    def close(self):
        self.conn.close()


# ==============================================================================
# CLIMATE LAYER
# ==============================================================================

def growing_degree_days(aoi, start: str, end: str, crop: str = "default"):
    """Accumulated heat for development, from ERA5-Land daily air temperature.
    GDD explains why the same crop on two planting dates behaves differently."""
    base = GDD_BASE_C.get(crop, GDD_BASE_C["default"])
    try:
        # Buffered to one native ERA5 pixel: reduceRegion counts a pixel only
        # when its CENTRE falls inside the region, so a 10 km reduction over a
        # 600 m field silently returns None. See dl.coarse_sampling_buffer_m.
        region = aoi.buffer(dl.coarse_sampling_buffer_m(ERA5_NATIVE_M))
        col = (ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
               .filterBounds(region).filterDate(start, end).select("temperature_2m"))
        if col.size().getInfo() == 0:
            return None
        total = col.map(lambda i: i.subtract(273.15).subtract(base).max(0).rename("gdd")
                        ).sum().reduceRegion(
            reducer=ee.Reducer.mean(), geometry=region, scale=ERA5_NATIVE_M,
            maxPixels=1e9, bestEffort=True).getInfo()
        return total.get("gdd")
    except Exception:
        return None


def heat_stress_days(aoi, start: str, end: str, crop: str = "default"):
    """Days on which maximum air temperature exceeded the crop's stress limit."""
    limit = HEAT_STRESS_C.get(crop, HEAT_STRESS_C["default"])
    try:
        region = aoi.buffer(dl.coarse_sampling_buffer_m(ERA5_NATIVE_M))
        col = (ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
               .filterBounds(region).filterDate(start, end).select("temperature_2m_max"))
        if col.size().getInfo() == 0:
            return None
        total = col.map(lambda i: i.subtract(273.15).gt(limit)).sum().reduceRegion(
            reducer=ee.Reducer.mean(), geometry=region, scale=ERA5_NATIVE_M,
            maxPixels=1e9, bestEffort=True).getInfo()
        v = total.get("temperature_2m_max")
        return int(round(v)) if v is not None else None
    except Exception:
        return None


def dry_spells(aoi, start: str, end: str) -> Optional[dict]:
    """
    Longest run of consecutive days below the rain floor.

    A season can deliver a normal total and still fail if it arrives in two
    downpours three weeks apart. Seasonal totals hide that; this does not.

    Pulls the whole daily region-mean series in ONE aggregate_array call, then
    hands it to decision_logic.longest_dry_run. (The first draft did one
    getInfo per day - up to ~400 blocking round-trips for one canal.)
    """
    try:
        region = aoi.buffer(dl.coarse_sampling_buffer_m(CHIRPS_NATIVE_M))
        col = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
               .filterBounds(region).filterDate(start, end))
        if col.size().getInfo() == 0:
            return None

        def day_mean(img):
            v = img.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=region, scale=CHIRPS_NATIVE_M,
                maxPixels=1e8, bestEffort=True).get("precipitation")
            return ee.Feature(None, {"p": v})

        vals = (ee.FeatureCollection(col.map(day_mean))
                .aggregate_array("p").getInfo())     # <-- one round trip
        vals = [v if v is not None else 0.0 for v in vals]
        longest = dl.longest_dry_run(vals, DRY_SPELL_RAIN_MM)
        return {"longest_dry_spell_days": longest,
                "flagged": longest >= DRY_SPELL_DAYS,
                "threshold_days": DRY_SPELL_DAYS,
                "rain_floor_mm": DRY_SPELL_RAIN_MM,
                "days_in_series": len(vals),
                "basis": "ARBITRARY: hand-chosen dry-spell length and rain floor"}
    except Exception:
        return None


def season_vs_history(aoi, season_start: str, season_end: str,
                      years_back: int = 10) -> Optional[dict]:
    """
    This season's rainfall against the same window in previous years.

    Absolute millimetres mean little without the site's own history. Builds the
    per-year totals server-side and pulls them in ONE aggregate_array call, then
    hands them to decision_logic.season_percentile_verdict. (The first draft did
    one getInfo per year.)
    """
    try:
        sm, sd = int(season_start[5:7]), int(season_start[8:10])
        em, ed = int(season_end[5:7]), int(season_end[8:10])
        cur_year = int(season_start[:4])
        # window crosses the new year when the end month is earlier than start.
        year_inc = 1 if em < sm else 0

        years = ee.List.sequence(cur_year - years_back, cur_year)

        def year_total(y):
            y = ee.Number(y)
            s = ee.Date.fromYMD(y, sm, sd)
            e = ee.Date.fromYMD(y.add(year_inc), em, ed)
            col = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
                   .filterBounds(aoi).filterDate(s, e))
            total = col.sum().reduceRegion(
                reducer=ee.Reducer.mean(), geometry=aoi, scale=5000,
                maxPixels=1e9, bestEffort=True).get("precipitation")
            return ee.Feature(None, {"year": y, "total": total})

        fc = ee.FeatureCollection(years.map(year_total))
        pairs = fc.aggregate_array("total").getInfo()   # <-- one round trip
        yrs = fc.aggregate_array("year").getInfo()
        by_year = {int(yr): t for yr, t in zip(yrs, pairs)}
        this = by_year.get(cur_year)
        hist = [t for y, t in by_year.items() if y != cur_year and t is not None]
        verdict = dl.season_percentile_verdict(this, hist)
        return verdict     # None if <3 history years, handled by caller
    except Exception:
        return None


def climate_context(aoi, start: str, end: str, crop: str = "default") -> dict:
    """Seasonal climate, and how this season compares with the site's own past."""
    return {
        "growing_degree_days": growing_degree_days(aoi, start, end, crop),
        "gdd_base_c": GDD_BASE_C.get(crop, GDD_BASE_C["default"]),
        "heat_stress_days": heat_stress_days(aoi, start, end, crop),
        "heat_stress_threshold_c": HEAT_STRESS_C.get(crop, HEAT_STRESS_C["default"]),
        "heat_stress_basis": "ARBITRARY: published crop figure, not a Sudanese trial",
        "dry_spells": dry_spells(aoi, start, end),
        "season_vs_history": season_vs_history(aoi, start, end),
        "sources": {"temperature": "ERA5-Land daily aggregates",
                    "rainfall": "CHIRPS daily"},
    }


# ==============================================================================
# GROUND OBSERVATIONS - phone photographs, and drone imagery later
# ==============================================================================

@dataclass
class GroundObservation:
    obs_id: str
    field_id: str
    observed_at: str
    lat: float
    lon: float
    photo_path: str
    source: str = "phone"            # phone | drone
    observer: str = ""
    crop: str = ""
    growth_stage: str = ""
    canopy_condition: str = ""       # healthy | patchy | yellowing | wilting
    weeds_present: Optional[bool] = None
    weed_cover_pct: Optional[float] = None
    pest_damage: Optional[bool] = None
    disease_signs: Optional[bool] = None
    # WHICH problem, not merely whether there was one. A boolean "disease
    # signs" tick cannot lift the disease ladder past an anomaly: only a NAME
    # can, because naming is the thing the satellite cannot do. The value is a
    # key from src/disease.py, or empty.
    problem: str = ""
    soil_surface: str = ""           # dry | moist | waterlogged | cracked | crusted
    salinity_signs: Optional[bool] = None
    water_reached_field: Optional[bool] = None
    days_since_irrigation: Optional[int] = None
    outlet_condition: str = ""       # open | blocked | damaged | silted
    notes: str = ""
    satellite_agreement: Optional[str] = None


class ObservationStore:
    """Ground observations and the link back to what the satellite said. Storing
    whether the two agreed accumulates, over a season, into an honest picture of
    where the indicators work - the difference between claiming accuracy and
    having measured it."""

    # Columns added after the first release. `CREATE TABLE IF NOT EXISTS` does
    # nothing at all to a table that already exists, so a database created
    # before one of these was added keeps its old shape for ever and the first
    # query that mentions the new column raises `no such column`.
    #
    # This was found by a live run, not by the test suite: every test builds a
    # fresh database in a temporary directory and therefore always gets the
    # newest schema. The only machine that could see it was one that had
    # actually been used - which is every real one.
    #
    # The failure was also badly placed. It came from inside the per-field
    # loop, so it killed the run AFTER minutes of Earth Engine work. Migration
    # happens when the store is opened, which is before any of that.
    MIGRATIONS = {
        "observations": {"problem": "TEXT"},
    }

    def __init__(self, path: str = "observations.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                obs_id TEXT PRIMARY KEY, field_id TEXT NOT NULL,
                observed_at TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
                photo_path TEXT NOT NULL, source TEXT NOT NULL, observer TEXT,
                crop TEXT, growth_stage TEXT, canopy_condition TEXT,
                weeds_present INTEGER, weed_cover_pct REAL,
                pest_damage INTEGER, disease_signs INTEGER, problem TEXT,
                soil_surface TEXT, salinity_signs INTEGER,
                water_reached_field INTEGER, days_since_irrigation INTEGER,
                outlet_condition TEXT, notes TEXT,
                satellite_ndvi REAL, satellite_cire REAL, satellite_agreement TEXT
            )""")
        self.migrate()
        self.conn.commit()

    def migrate(self) -> list:
        """
        Bring an older database up to the current schema. Returns what it added.

        Additive only: new nullable columns. Existing rows keep every value
        they had and gain NULL for the new column, which is the truthful answer
        - nobody recorded a problem on an observation saved before there was a
        field for one.

        Nothing here drops, renames or rewrites a column. A migration that can
        lose a season of somebody's scouting records is not worth the tidiness
        it buys, and this one runs unattended every time the store is opened.
        """
        added = []
        for table, cols in self.MIGRATIONS.items():
            have = {r[1] for r in
                    self.conn.execute(f"PRAGMA table_info({table})")}
            if not have:
                continue                       # the table itself is new
            for name, decl in cols.items():
                if name not in have:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    added.append(f"{table}.{name}")
        if added:
            self.conn.commit()
            print(f"observations.db: added {', '.join(added)} "
                  "(existing rows keep their values)")
        return added

    def add(self, obs: GroundObservation, satellite: Optional[dict] = None) -> str:
        sat = satellite or {}
        self.conn.execute(
            "INSERT OR REPLACE INTO observations (obs_id, field_id, observed_at,"
            " lat, lon, photo_path, source, observer, crop, growth_stage,"
            " canopy_condition, weeds_present, weed_cover_pct, pest_damage,"
            " disease_signs, problem, soil_surface, salinity_signs,"
            " water_reached_field,"
            " days_since_irrigation, outlet_condition, notes, satellite_ndvi,"
            " satellite_cire, satellite_agreement) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (obs.obs_id, obs.field_id, obs.observed_at, obs.lat, obs.lon,
             obs.photo_path, obs.source, obs.observer, obs.crop, obs.growth_stage,
             obs.canopy_condition, _b(obs.weeds_present), obs.weed_cover_pct,
             _b(obs.pest_damage), _b(obs.disease_signs), obs.problem,
             obs.soil_surface,
             _b(obs.salinity_signs), _b(obs.water_reached_field),
             obs.days_since_irrigation, obs.outlet_condition, obs.notes,
             sat.get("NDVI"), sat.get("CIre"), obs.satellite_agreement))
        self.conn.commit()
        return obs.obs_id

    def agreement_summary(self, crop: Optional[str] = None) -> dict:
        """How often the satellite reading matched what a person saw - the only
        figure in the platform that describes the platform's own reliability."""
        q = ("SELECT satellite_agreement, COUNT(*) FROM observations "
             "WHERE satellite_agreement IS NOT NULL")
        args: tuple = ()
        if crop:
            q += " AND crop = ?"
            args = (crop,)
        q += " GROUP BY satellite_agreement"
        rows = self.conn.execute(q, args).fetchall()
        counts = {r[0]: r[1] for r in rows}
        clear = ("AGREE", "SATELLITE_WORSE", "GROUND_WORSE")
        scored = sum(counts.get(k, 0) for k in clear)
        unclear = counts.get("UNCLEAR", 0)
        # The reliability figure is computed ONLY from clear cases (integrity
        # rule 8). UNCLEAR observations are reported separately, never folded into
        # the rate - a forced verdict there would corrupt the one number that
        # describes the platform's own accuracy.
        if not scored:
            return {"available": False, "unclear": unclear,
                    "reason": ("no CLEAR satellite/ground comparisons yet"
                               + (f" ({unclear} unclear)" if unclear else ""))}
        return {"available": True, "total": scored, "unclear": unclear,
                "breakdown": {k: v for k, v in counts.items()},
                "agreement_rate": round(counts.get("AGREE", 0) / scored, 3)}

    def scouting_for(self, field_id: str) -> list:
        """Named findings for one field, newest last, in the shape the disease
        ladder expects.

        Only rows carrying a NAMED problem are returned. "I walked the field
        and ticked disease signs" is not "I found anthracnose", and the rung
        that names a disease must not be lifted by a checkbox."""
        rows = self.conn.execute(
            "SELECT problem, observed_at, observer FROM observations "
            "WHERE field_id = ? AND problem IS NOT NULL AND problem != '' "
            "ORDER BY observed_at", (field_id,)).fetchall()
        return [{"problem": r[0], "observed_at": r[1], "observer": r[2] or ""}
                for r in rows]

    def close(self):
        self.conn.close()


def _b(v: Optional[bool]) -> Optional[int]:
    return None if v is None else int(v)


def satellite_for_field(report: dict, field_name: str) -> dict:
    """What the satellite said about this field, in the shape the comparison
    wants. Empty where the field is not in the report or was not measured."""
    for rec in (report or {}).get("fields", []):
        if rec.get("name") != field_name:
            continue
        vig = (rec.get("crop_health") or {}).get("readings", {}).get(
            "vigour", {})
        out = {}
        if vig.get("status") == "OK":
            out["NDVI"] = vig.get("value")
        nut = rec.get("nutrition") or {}
        if nut.get("cire") is not None:
            out["CIre"] = nut.get("cire")
        return out
    return {}


def reference_p25(report: dict) -> Optional[float]:
    """
    The 25th percentile of vigour across the fields in this report.

    This is the line the agreement verdict calls "poor". It is the FARM's own
    lower quartile, not a scheme-wide norm, and it means what it says: a field
    in the worst quarter of the fields measured on the same dates by the same
    instrument. On a farm of three fields it is nearly meaningless, which is
    why the caller is given None rather than a number when there are too few.
    """
    vals = sorted(
        v for v in ((rec.get("crop_health") or {}).get("readings", {})
                    .get("vigour", {}).get("value")
                    for rec in (report or {}).get("fields", []))
        if v is not None)
    if len(vals) < 4:
        return None
    idx = max(0, int(round(0.25 * (len(vals) - 1))))
    return vals[idx]


def score_observation(obs: GroundObservation, report: dict) -> dict:
    """
    Work out whether the satellite agreed with the observer, at save time.

    WHY THIS FUNCTION HAD TO EXIST
    The scouting form saved observations with no satellite side at all, so
    `satellite_agreement` was NULL on every row ever written and the agreement
    rate could never accumulate. The platform's single most credible figure -
    the one that describes its own accuracy rather than claiming it - was
    structurally unable to leave zero, and nothing said so: the screen simply
    reported "no clear comparisons yet" for ever.

    Returns the verdict and everything that went into it, so an UNCLEAR can be
    explained rather than merely counted.
    """
    sat = satellite_for_field(report, obs.field_id)
    p25 = reference_p25(report)
    verdict = dl.agreement_verdict(sat.get("NDVI"), obs.canopy_condition, p25)
    why = ""
    if verdict == "UNCLEAR":
        if sat.get("NDVI") is None:
            why = ("the satellite has no usable vigour reading for this field, "
                   "so there is nothing to compare the observation with")
        elif p25 is None:
            why = ("fewer than four measured fields in this report, so there "
                   "is no lower quartile to call 'poor'")
        else:
            why = ("the canopy condition recorded is not one the comparison "
                   "can score - use healthy, patchy, yellowing or wilting")
    return {"verdict": verdict, "satellite": sat, "reference_p25": p25,
            "canopy_condition": obs.canopy_condition, "reason": why}


def compare_with_satellite(obs: GroundObservation, satellite_indices: dict,
                           scheme_p25: float) -> str:
    """Did the satellite see what the observer saw? Delegates the verdict to the
    tested decision_logic.agreement_verdict, which returns UNCLEAR for any case
    that is not clean on both sides."""
    return dl.agreement_verdict(satellite_indices.get("NDVI"),
                                obs.canopy_condition, scheme_p25)


# ==============================================================================
# DRONE - the layer above phones, deliberately kept as a stub
# ==============================================================================

DRONE_ROADMAP = {
    "status": "PLANNED - not implemented",
    "why_it_matters": (
        "Satellites rank fields; phones diagnose one spot; a drone maps the whole "
        "field at centimetre scale, where weed patches, waterlogged corners and "
        "blocked outlets become geometry rather than a hunch."),
    "what_becomes_possible": [
        "Weed patch mapping and species classification, which Sentinel-2 at 10 m "
        "cannot do (weed patches are sub-pixel and weeds and crop are both green).",
        "Plant counting and gap detection for stand establishment.",
        "Within-field variability at the scale variable-rate application needs.",
        "Canal and outlet inspection without walking the reach.",
        "Direct validation imagery for the satellite indicators."],
    "sensor_options": {
        "RGB": "cheapest; enough for weed patches, gaps and structural damage",
        "multispectral_red_edge": "same chlorophyll indices as Sentinel-2 but at "
                                  "centimetres - the natural bridge between scales",
        "thermal": "field-scale water stress and leak detection along canals"},
    "integration_path": [
        "Store drone flights as GroundObservation with source='drone'.",
        "Orthomosaic to COG, load as a GeoLibre layer.",
        "Resample drone indices to Sentinel-2 pixels to check the satellite "
        "against a known-good reference.",
        "Train weed/gap models on drone imagery, then look for the coarse "
        "signature they leave in Sentinel-2."],
    "honest_constraints": [
        "Regulatory permission for drone flight in Sudan must be settled first.",
        "Coverage is ~20-50 ha per battery: a scheme the size of Gezira cannot be "
        "flown, so drones are for verification and training data, never routine "
        "wide-area monitoring.",
        "Radiometric calibration is required before drone indices can be compared "
        "with satellite indices at all."],
}


# ==============================================================================
# NUTRITION ASSEMBLY
# ==============================================================================

def nutrition_status(field_indices: dict, scheme_stats: dict, crop: str,
                     reference_indices: Optional[dict] = None,
                     calib: Optional[CalibrationStore] = None) -> NutritionResult:
    """
    Report crop nutrition at the strongest level the evidence supports, no higher.

      relative     always: where this field sits in the scheme
      sufficiency  with a reference strip: field vs its own saturated strip
      calibrated   with a fitted, in-limit model: absolute leaf N %, with error
    """
    cire = field_indices.get("CIre")
    if cire is None:
        return NutritionResult(
            status="NOT AVAILABLE",
            reason="no usable red-edge indices for this field in the window")

    res = NutritionResult(status="OK", chlorophyll_indices=field_indices)

    rel = dl.relative_condition(cire, scheme_stats.get("cire_p25"),
                                scheme_stats.get("cire_p75"))
    if rel["status"] == "OK":
        res.relative_condition = rel["condition"]
        res.percentile_in_scheme = rel["band"]
    else:
        res.relative_condition = "NOT AVAILABLE"
        res.reason = rel["reason"]

    if reference_indices and reference_indices.get("CIre"):
        suff = dl.sufficiency_reading(cire, reference_indices["CIre"],
                                      SUFFICIENCY_DEFICIENT, SUFFICIENCY_MARGINAL)
        if suff["status"] == "OK":
            res.sufficiency_index = suff["sufficiency_index"]
            res.sufficiency_reading = suff["reading"]
            res.claim_level = "sufficiency"

    if calib:
        pred = calib.predict(crop, field_indices)
        if pred and pred.get("available"):
            res.nitrogen_pct = pred["nitrogen_pct"]
            res.nitrogen_confidence = pred["confidence"]
            res.claim_level = "calibrated"
        elif pred:
            res.reason = pred.get("reason")

    return res
