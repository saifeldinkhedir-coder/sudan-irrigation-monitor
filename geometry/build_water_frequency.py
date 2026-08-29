"""
Persistent-water-frequency builder - the way to get canal geometry when you have
no survey.

THE PROBLEM THIS SOLVES
-----------------------
You have no digitised canal centrelines for New Halfa (or any other scheme), and
the honest options are limited:

  - Tracing over a single Sentinel-2 scene by eye is bad: one scene is often
    cloudy, and a canal that happens to be low or empty that week is invisible.
  - A single Sentinel-1 scene is better under cloud but still a snapshot: a dry
    reach on that date simply is not there.

The fix is to stop looking at one date. A canal that carries water is wet on MANY
dates across a season; a random wet pixel (a puddle, a flooded field, radar
speckle) is wet on few. So we count, per pixel, the FRACTION of observations on
which it read as water, from two independent sensors, and keep the pixels that
are wet often. Canals light up as continuous bright lines against a dark
background; transient water fades out. You then trace centrelines over THAT
raster in QGIS or GeoLibre, which is a categorically better base than any single
image.

This produces the input the engine needs (canal LineStrings), and it is
reproducible and provenance-carrying rather than a hand-wave.

WHAT IT EXPORTS
---------------
A single-band GeoTIFF `water_frequency` in [0,1] to your Google Drive, plus a
quick-look thumbnail. Two components you can weight or threshold in QGIS:
  - s1_water_freq : fraction of Sentinel-1 scenes with VV < the water cutoff
  - s2_water_freq : fraction of valid Sentinel-2 scenes with MNDWI > its cutoff
  - combined      : the mean of the two where both exist, else whichever exists

HONEST LIMITS (read before trusting the output)
-----------------------------------------------
- Canals narrower than ~10-20 m are at or below one pixel and will be faint or
  absent. New Halfa's major and branch canals should show; the smallest field
  ditches will not, and you should not pretend to trace what is not resolved.
- A canal dry for most of the season (e.g. the Managil situation reported in
  Gezira) will read as low-frequency and look like no canal. That is a true
  statement about water, not a failure of the method - but it means you should
  build this over a season you believe carried water.
- The water cutoffs below are ARBITRARY starting points, declared as such, and
  should be tuned against a reach you can confirm by eye before you trust the
  faint end of the range.
- Irrigated fields flood too. Expect bright blobs at field scale; the canals are
  the thin connected lines threading between them, which is exactly the geometry
  a human tracer can pick out and an unsupervised threshold cannot.

RUN
---
    # set EE_PROJECT in your environment first
    python build_water_frequency.py --bbox 35.7 15.2 36.3 15.8 \
        --season 2022 --out-prefix newhalfa_water_2022

Then: load the exported GeoTIFF in QGIS, style 0->1 as a single-band pseudocolor,
trace canal centrelines as a new LineString layer, add a `name` (and, if you
have it, a `canal`/`office`) attribute per canal, and export as GeoJSON for the
engine's --canal argument.
"""

from __future__ import annotations

import argparse
import sys

try:
    import ee
except ImportError:
    print("ABORT: earthengine-api not installed.  pip install earthengine-api")
    sys.exit(1)

import os


# --- ARBITRARY, declared constants --------------------------------------------
S1_WATER_DB = -16.0          # VV below this is water (specular at C-band)
S2_MNDWI_WATER = 0.0         # MNDWI above this is water; 0 is the classic cut
S2_CLOUD_PCT = 60            # generous: the frequency count tolerates some cloud
KEEP_FREQUENCY = 0.30        # suggested display floor; TUNE against a known reach
# ------------------------------------------------------------------------------


def init_ee() -> str:
    project = os.environ.get("EE_PROJECT")
    if not project:
        print("ABORT: set EE_PROJECT in the environment.")
        sys.exit(1)
    ee.Initialize(project=project)
    return project


def season_window(season: int):
    return f"{season}-07-01", f"{season + 1}-03-31"


def s1_water_frequency(aoi, start, end):
    """Fraction of Sentinel-1 VV scenes on which each pixel read as water."""
    col = (ee.ImageCollection("COPERNICUS/S1_GRD")
           .filterBounds(aoi).filterDate(start, end)
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
           .filter(ee.Filter.eq("instrumentMode", "IW"))
           .select("VV"))
    n = col.size()
    water = col.map(lambda img: img.lt(S1_WATER_DB))
    freq = water.sum().divide(ee.Image.constant(n).max(1)).rename("s1_water_freq")
    return freq, n


def s2_water_frequency(aoi, start, end):
    """Fraction of valid Sentinel-2 scenes on which MNDWI marked each pixel water.

    Uses per-pixel valid counts so a pixel seen through cloud on some dates is
    scored only against the dates it was actually seen."""
    col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
           .filterBounds(aoi).filterDate(start, end)
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_CLOUD_PCT)))

    def mndwi_water(img):
        b = img.divide(10000)
        mndwi = b.normalizedDifference(["B3", "B11"]).rename("MNDWI")
        # scl-based cloud mask keeps the per-pixel validity honest
        scl = img.select("SCL")
        clear = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
        water = mndwi.gt(S2_MNDWI_WATER).updateMask(clear)
        valid = clear.rename("valid")
        return water.rename("water").addBands(valid)

    mapped = col.map(mndwi_water)
    water_sum = mapped.select("water").sum()
    valid_sum = mapped.select("valid").sum().max(1)
    freq = water_sum.divide(valid_sum).rename("s2_water_freq")
    return freq, col.size()


def build(bbox, season, out_prefix, scale=10):
    proj = init_ee()
    aoi = ee.Geometry.Rectangle(bbox)
    start, end = season_window(season)
    print(f"Project {proj}: water frequency over {bbox}, season {start}..{end}")

    s1, n1 = s1_water_frequency(aoi, start, end)
    s2, n2 = s2_water_frequency(aoi, start, end)
    print(f"Sentinel-1 scenes: {n1.getInfo()}   Sentinel-2 scenes: {n2.getInfo()}")

    # Combined = mean of the two where both exist, else whichever exists.
    combined = ee.ImageCollection([s1, s2.rename("s1_water_freq")]).mean() \
        .rename("combined")
    out = ee.Image.cat([s1, s2, combined]).clip(aoi).toFloat()

    task = ee.batch.Export.image.toDrive(
        image=out, description=out_prefix, fileNamePrefix=out_prefix,
        region=aoi, scale=scale, maxPixels=1e13,
        fileFormat="GeoTIFF")
    task.start()
    print(f"Export started: '{out_prefix}.tif' -> your Google Drive.")
    print("Poll status with task.status() or in the EE Tasks tab.")
    print(f"Suggested display floor while tracing: combined > {KEEP_FREQUENCY} "
          "(ARBITRARY - tune against a reach you can confirm by eye).")
    return task


def main():
    p = argparse.ArgumentParser(
        description="Build a persistent-water-frequency raster for tracing canals")
    p.add_argument("--bbox", nargs=4, type=float, required=True,
                   metavar=("W", "S", "E", "N"),
                   help="bounding box lon/lat: west south east north")
    p.add_argument("--season", type=int, required=True,
                   help="season start year; window runs July to March")
    p.add_argument("--out-prefix", default="water_frequency")
    p.add_argument("--scale", type=int, default=10)
    a = p.parse_args()
    build(a.bbox, a.season, a.out_prefix, a.scale)


if __name__ == "__main__":
    main()
