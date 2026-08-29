"""
Pre-flight sensor check: does every dataset this engine names still exist, cover
the area, and carry the bands the code asks for?

WHY THIS IS A SEPARATE TOOL
---------------------------
Running the full engine to find out whether MODIS/061/MOD16A2GF is still the
current collection id gives you one opaque failure at the end of a long run,
after quota has been spent. This checks each sensor independently and reports
which ones work, which are empty over your area and season, and which have a
band the code expects but the dataset does not have.

The three failure modes it separates, which look identical from inside the
engine:

  MISSING     the collection id is wrong or the dataset was retired. Earth
              Engine renames and versions collections (MOD16A2 -> MOD16A2GF,
              GSW1_3 -> GSW1_4, GRACE V03 -> V04), and code that names a retired
              id fails at the first call.
  EMPTY       the collection exists but has no images over this area in this
              window. That is a true statement about coverage, not a bug -
              Landsat thermal over a small area in a cloudy season genuinely
              returns few or no scenes.
  BAND        the collection and images exist, but a band the code selects is
              absent or renamed. This is the one that produces confident wrong
              numbers rather than errors, because a reducer over a missing band
              returns null and a careless caller reads that as zero.

RUN
---
    export EE_PROJECT=your-ee-project
    python geometry/check_sensors.py --bbox 33.0 14.2 33.5 14.6 --season 2022
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import ee
except ImportError:
    print("ABORT: earthengine-api not installed.  pip install earthengine-api")
    sys.exit(1)


# (label, collection id, bands the engine selects, notes)
SENSORS = [
    ("Sentinel-2 optical", "COPERNICUS/S2_SR_HARMONIZED",
     ["B3", "B4", "B8", "B11", "SCL"],
     "NDVI, EVI, NDMI, MNDWI, cloud mask"),
    ("Sentinel-2 red-edge", "COPERNICUS/S2_SR_HARMONIZED",
     ["B5", "B6", "B7"],
     "CIre, MTCI, NDRE, S2REP - the nutrition ladder"),
    ("Sentinel-1 radar", "COPERNICUS/S1_GRD",
     ["VV"],
     "canal standing water, cloud-penetrating"),
    ("Landsat 8 thermal", "LANDSAT/LC08/C02/T1_L2",
     ["ST_B10"],
     "land surface temperature"),
    ("Landsat 9 thermal", "LANDSAT/LC09/C02/T1_L2",
     ["ST_B10"],
     "land surface temperature"),
    ("CHIRPS rainfall", "UCSB-CHG/CHIRPS/DAILY",
     ["precipitation"],
     "the cause-separating dataset - drought vs network failure"),
    ("ERA5-Land daily", "ECMWF/ERA5_LAND/DAILY_AGGR",
     ["temperature_2m_max", "temperature_2m_min", "dewpoint_temperature_2m",
      "u_component_of_wind_10m", "v_component_of_wind_10m",
      "surface_net_solar_radiation_sum", "surface_net_thermal_radiation_sum",
      "surface_pressure"],
     "FAO-56 ET0 inputs, GDD, heat stress"),
    ("MODIS evapotranspiration", "MODIS/061/MOD16A2GF",
     ["ET"],
     "command-scale water consumption"),
    ("GRACE/GRACE-FO storage", "NASA/GRACE/MASS_GRIDS_V04/MASCON",
     ["lwe_thickness"],
     "REGIONAL CONTEXT ONLY - ~300 km footprint; the LAND product ends 2017"),
    # GFS band sets vary between images; first() can land on a 6-band image
    # that genuinely lacks precipitation, so only the always-present band is
    # asserted here and the engine reduces each band independently.
    ("NOAA GFS forecast", "NOAA/GFS0P25",
     ["temperature_2m_above_ground"],
     "7-day outlook, ~28 km; precipitation band is not on every image"),
    ("JRC surface water", "JRC/GSW1_4/MonthlyHistory",
     ["water"],
     "rangeland water points / hafirs"),
]

# Soil texture is an Image, not an ImageCollection, so it is checked separately.
SOIL_IMAGE = ("OpenLandMap soil texture",
              "OpenLandMap/SOL/SOL_TEXTURE-CLASS_USDA-TT_M/v02",
              ["b0"], "attribution control for soil")


def init_ee() -> str:
    project = os.environ.get("EE_PROJECT")
    if not project:
        print("ABORT: set EE_PROJECT in the environment first.\n"
              "  export EE_PROJECT=your-ee-project     (bash)\n"
              "  $env:EE_PROJECT='your-ee-project'     (PowerShell)")
        sys.exit(1)
    ee.Initialize(project=project)
    return project


def check_collection(label, cid, bands, note, aoi, start, end) -> dict:
    """One sensor. Never raises - a failure is a result, not a crash."""
    out = {"label": label, "id": cid, "note": note}
    try:
        col = ee.ImageCollection(cid).filterBounds(aoi).filterDate(start, end)
        n = col.size().getInfo()
    except Exception as e:
        out.update({"status": "MISSING", "detail": str(e)[:160]})
        return out

    out["n_images"] = n
    if n == 0:
        # Distinguish "no coverage in this window" from "collection is empty
        # everywhere", which would point at a wrong id rather than at coverage.
        try:
            total = ee.ImageCollection(cid).limit(1).size().getInfo()
        except Exception:
            total = 0
        out.update({
            "status": "EMPTY",
            "detail": ("collection exists but has no images over this area in "
                       "this window" if total else
                       "collection returned nothing at all - check the id")})
        return out

    try:
        available = ee.Image(col.first()).bandNames().getInfo()
    except Exception as e:
        out.update({"status": "BAND", "detail": f"could not read bands: {e}"[:160]})
        return out

    missing = [b for b in bands if b not in available]
    if missing:
        out.update({"status": "BAND", "missing": missing,
                    "detail": f"available: {', '.join(available[:12])}"
                              f"{' ...' if len(available) > 12 else ''}"})
        return out

    out["status"] = "OK"
    return out


def check_image(label, iid, bands, note) -> dict:
    out = {"label": label, "id": iid, "note": note}
    try:
        available = ee.Image(iid).bandNames().getInfo()
    except Exception as e:
        out.update({"status": "MISSING", "detail": str(e)[:160]})
        return out
    missing = [b for b in bands if b not in available]
    out.update({"status": "BAND" if missing else "OK"})
    if missing:
        out["missing"] = missing
        out["detail"] = f"available: {', '.join(available[:12])}"
    return out


def main():
    p = argparse.ArgumentParser(
        description="Check every dataset the engine names, one at a time")
    p.add_argument("--bbox", nargs=4, type=float,
                   default=[33.0, 14.2, 33.5, 14.6],
                   metavar=("W", "S", "E", "N"),
                   help="lon/lat bounding box; default is a Gezira tile")
    p.add_argument("--season", type=int, default=2022,
                   help="season start year; window runs July to March")
    a = p.parse_args()

    project = init_ee()
    aoi = ee.Geometry.Rectangle(a.bbox)
    start, end = f"{a.season}-07-01", f"{a.season + 1}-03-31"

    print("=" * 74)
    print(f"Sensor pre-flight   project {project}")
    print(f"Area {a.bbox}   season {start} .. {end}")
    print("=" * 74)

    results = []
    for label, cid, bands, note in SENSORS:
        r = check_collection(label, cid, bands, note, aoi, start, end)
        results.append(r)
        mark = {"OK": "OK     ", "EMPTY": "EMPTY  ",
                "BAND": "BAND   ", "MISSING": "MISSING"}[r["status"]]
        n = r.get("n_images")
        print(f"{mark} {label:28} {'' if n is None else f'{n:>5} images'}")
        if r["status"] != "OK":
            if r.get("missing"):
                print(f"        missing bands: {r['missing']}")
            print(f"        {r.get('detail', '')}")

    r = check_image(*SOIL_IMAGE)
    results.append(r)
    print(f"{'OK     ' if r['status'] == 'OK' else r['status']:8}"
          f"{SOIL_IMAGE[0]}")
    if r["status"] != "OK":
        print(f"        {r.get('detail', '')}")

    print()
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"{counts.get('OK', 0)} OK, {counts.get('EMPTY', 0)} empty, "
          f"{counts.get('BAND', 0)} band problems, "
          f"{counts.get('MISSING', 0)} missing")

    if counts.get("MISSING") or counts.get("BAND"):
        print("\nMISSING or BAND means the code names something the catalogue no "
              "longer has. Fix those before a real run: a reducer over an absent "
              "band returns null, and a careless caller reads null as zero.")
        sys.exit(1)
    if counts.get("EMPTY"):
        print("\nEMPTY is not necessarily a fault. Landsat thermal and GRACE-FO "
              "genuinely have sparse or no coverage over a small area in a short "
              "window. The engine reports these as NOT AVAILABLE with a reason, "
              "which is the correct behaviour.")
    print("\nAll named datasets resolve. This says the plumbing reaches real "
          "data; it says nothing yet about whether the numbers are right.")


if __name__ == "__main__":
    main()
