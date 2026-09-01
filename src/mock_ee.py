"""
A minimal, deterministic mock of the Google Earth Engine Python API - enough for
engine.analyse() and the nutrition/climate module to run END TO END with no
network, no auth, and no EE quota.

WHAT THIS IS FOR, AND WHAT IT IS NOT
------------------------------------
It exists to test the PLUMBING: that every function signature lines up, every
reduceRegion result is read with the right key, every branch assembles a
well-formed, JSON-serialisable record, and nothing raises on a full run. That is
exactly the gap the pure-logic tests cannot cover.

It is NOT a validation of Earth Engine. The numbers it returns are deterministic
synthetic values, not real imagery, and it makes no claim that real GEE behaves
this way. A green assembly test means "the pipeline is wired correctly", never
"the measurements are correct". Correctness of the measurements can only be
established against real imagery with your authenticated EE_PROJECT.

Values are produced from a SHA-256 hash of a descriptive key, so a given
band/geometry/reducer always yields the same number - runs are reproducible.
"""

from __future__ import annotations

import hashlib

# Knob used by the command-area resolution test to control how many polygons
# "intersect" a canal buffer. Default 0 (nothing intersects).
FILTERBOUNDS_COUNT = 0


def _h(*parts) -> float:
    """Deterministic pseudo-value in [0,1) from the parts."""
    s = "|".join(str(p) for p in parts)
    d = hashlib.sha256(s.encode()).hexdigest()
    return int(d[:8], 16) / 0xFFFFFFFF


# ---------------------------------------------------------------- values ------

def _band_mean(band: str, geomspec: str) -> float:
    r = _h("mean", band, geomspec)
    ranges = {
        "NDVI": (0.20, 0.75), "EVI": (0.10, 0.60), "NDMI": (0.00, 0.40),
        "MNDWI": (-0.30, 0.05), "VV": (0.02, 0.45),        # VV here = water frac
        "LST": (24.0, 44.0), "precipitation": (0.0, 220.0),
        "ET": (60.0, 380.0), "gdd": (400.0, 2600.0),
        "temperature_2m_max": (0.0, 40.0),
        "CIre": (0.5, 4.0), "MTCI": (0.5, 5.0), "NDRE": (0.1, 0.6),
        "S2REP": (700.0, 740.0), "nd": (0.2, 0.75),
        "lwe_thickness_csr": (-8.0, 6.0), "b0": (1.0, 11.0),
    }
    lo, hi = ranges.get(band, (0.0, 1.0))
    return lo + (hi - lo) * r


def _band_count(band: str, geomspec: str) -> float:
    # constant band anchors the denominator; other bands sit just below it so an
    # observed-fraction lands in a plausible 0.7-1.0 range.
    if band == "constant":
        return 12000.0
    return 12000.0 * (0.7 + 0.3 * _h("count", band, geomspec))


def _coord_in(band: str, coords, geomspec: str) -> float:
    """A deterministic point INSIDE the given polygon.

    The anomaly patch's centroid is turned into a compass bearing from the
    field's centre, so a coordinate drawn from a hash rather than from the
    geometry would put the patch outside its own field and produce a direction
    that means nothing. This walks the outer ring, takes its bounding box, and
    picks a repeatable point inside it.
    """
    ring = None
    if coords:
        ring = coords[0] if isinstance(coords[0][0], (list, tuple)) else coords
    if not ring:
        return 33.0 if band == "longitude" else 14.4
    idx = 0 if band == "longitude" else 1
    vals = [float(p[idx]) for p in ring]
    lo, hi = min(vals), max(vals)
    return lo + (hi - lo) * (0.15 + 0.7 * _h("coord", band, geomspec))


def _histogram(band: str, geomspec: str) -> dict:
    """A deterministic, mostly-bimodal histogram so Otsu has something real to
    split (bare soil hump low, crop hump high)."""
    mids = [round(-0.1 + 0.05 * i, 3) for i in range(20)]     # -0.1 .. 0.85
    r = _h("hist", band, geomspec)
    counts = []
    for m in mids:
        low = 60.0 * pow(2.718, -((m - 0.05) ** 2) / 0.01)
        high = (40.0 + 40.0 * r) * pow(2.718, -((m - 0.6) ** 2) / 0.02)
        counts.append(low + high)
    return {"histogram": [round(c, 2) for c in counts], "bucketMeans": mids}


# ---------------------------------------------------------------- objects -----

class _Value:
    def __init__(self, v):
        self._v = v
    def getInfo(self):
        return self._v
    def get(self, k):
        if isinstance(self._v, dict):
            return self._v.get(k)
        return None


class Reducer:
    def __init__(self, kind, params=None):
        self.kind = kind
        self.params = params or {}
    @staticmethod
    def mean(): return Reducer("mean")
    @staticmethod
    def sum(): return Reducer("sum")
    @staticmethod
    def first(): return Reducer("first")
    @staticmethod
    def percentile(pcts): return Reducer("percentile", {"pcts": pcts})
    @staticmethod
    def histogram(maxBuckets=None): return Reducer("histogram")


class Filter:
    @staticmethod
    def lt(*a, **k): return ("lt", a)
    @staticmethod
    def gt(*a, **k): return ("gt", a)
    @staticmethod
    def eq(*a, **k): return ("eq", a)
    @staticmethod
    def listContains(*a, **k): return ("listContains", a)


class Geometry:
    def __init__(self, spec="geom", coords=None):
        if isinstance(spec, dict):
            coords = spec.get("coordinates")
            spec = f"geom:{spec.get('type')}:{str(coords)[:40]}"
        self.spec = str(spec)
        self._coords = coords
    def buffer(self, d):
        return Geometry(f"buffer({self.spec},{d})", self._coords)
    def coordinates(self):
        return _Value(self._coords or [])
    @staticmethod
    def LineString(coords):
        return Geometry(f"line:{str(coords)[:40]}", coords)
    @staticmethod
    def Rectangle(bbox):
        return Geometry(f"rect:{bbox}")


class Feature:
    def __init__(self, geom=None, props=None):
        self.geom = geom
        self.props = props or {}


class Image:
    def __init__(self, bands=None, spec="img"):
        # a generous default band set so select()/reduceRegion always have
        # something to return; real EE would enforce band presence.
        self.bands = list(bands) if bands else [
            "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B11",
            "VV", "LST", "ET", "precipitation", "gdd",
            "temperature_2m", "temperature_2m_max", "lwe_thickness_csr", "b0"]
        self.spec = spec
        self._mask = False

    # --- band-producing ops ---
    def divide(self, x): return self
    def add(self, x): return self
    def subtract(self, x): return self
    def multiply(self, x): return self
    def max(self, x): return self
    def min(self, x): return self
    def lt(self, x): return self
    def gt(self, x): return self
    def eq(self, x): return self
    def neq(self, x): return self
    def And(self, x): return self
    def updateMask(self, x): return self
    def copyProperties(self, *a, **k): return self
    def date(self):
        return _MillisDate(self.spec)

    def mask(self):
        m = Image(self.bands, self.spec + ":mask")
        m._mask = True
        return m

    def normalizedDifference(self, pair):
        return Image(["nd"], f"nd({pair})")

    def expression(self, expr, d=None):
        return Image(["exp"], "expr")

    def rename(self, name):
        names = [name] if isinstance(name, str) else list(name)
        return Image(names, "renamed")

    def select(self, sel):
        sel = [sel] if isinstance(sel, str) else list(sel)
        img = Image(sel, self.spec + f":select{sel}")
        img._mask = self._mask
        return img

    def clip(self, geom): return self
    def toFloat(self): return self

    def reduceRegion(self, reducer=None, geometry=None, scale=None,
                     maxPixels=None, bestEffort=None, **kw):
        gspec = geometry.spec if isinstance(geometry, Geometry) else "none"
        gcoords = geometry._coords if isinstance(geometry, Geometry) else None
        out = {}
        for b in self.bands:
            # Coordinates have to come from the geometry rather than from a
            # hash, or the anomaly patch would sit outside the field it is
            # supposed to be inside and the bearing would be nonsense.
            if b in ("longitude", "latitude"):
                out[b] = _coord_in(b, gcoords, gspec)
            elif b == "area":
                # Masked: the patch. Unmasked: the whole field. Both in m2.
                out[b] = 12000.0 * (0.05 + 0.25 * _h("patch", gspec)) \
                    if self._mask else 400000.0
            elif reducer.kind == "mean":
                out[b] = _band_mean(b, gspec)
            elif reducer.kind == "sum":
                out[b] = _band_count(b, gspec) if (self._mask or b == "constant") \
                    else _band_mean(b, gspec) * 100.0
            elif reducer.kind == "first":
                out[b] = round(_band_mean(b, gspec))
            elif reducer.kind == "percentile":
                base = _band_mean(b, gspec)
                spread = 0.08 + 0.05 * _h("spread", b, gspec)
                for p in reducer.params["pcts"]:
                    frac = (p - 50) / 34.0            # ~ z for 16/50/84
                    out[f"{b}_p{p}"] = base + frac * spread
            elif reducer.kind == "histogram":
                out[b] = _histogram(b, gspec)
        return _Value(out)

    # --- statics ---
    @staticmethod
    def constant(v):
        return Image(["constant"], f"const({v})")

    @staticmethod
    def pixelArea():
        """Per-pixel area in square metres. Summed over a masked image, this is
        how the anomaly scan measures a patch."""
        return Image(["area"], "pixelArea")

    @staticmethod
    def pixelLonLat():
        """Per-pixel coordinates. Averaged over a masked image, this is where
        the patch is - which is what turns a number into somewhere to walk."""
        return Image(["longitude", "latitude"], "pixelLonLat")

    @staticmethod
    def cat(imgs):
        bands = []
        for im in imgs:
            bands += im.bands
        return Image(bands, "cat")


class _MillisDate:
    def __init__(self, spec):
        self.spec = spec
    def millis(self):
        return _Value(int(1.6e12 + 8.64e7 * _h("date", self.spec) * 200))


class ImageCollection:
    _SIZES = {"S2": 8, "S1": 5, "LANDSAT": 3, "CHIRPS": 180, "MODIS": 20,
              "ERA5": 180, "GRACE": 2}

    def __init__(self, src=None):
        if isinstance(src, str):
            self.dataset = src
        elif isinstance(src, list):
            self.dataset = "LIST"
        else:
            self.dataset = "MERGED"

    def _size(self):
        for key, n in self._SIZES.items():
            if key in (self.dataset or ""):
                return n
        return 6

    def filterBounds(self, *a): return self
    def filterDate(self, *a): return self
    def filter(self, *a): return self
    def select(self, *a): return self
    def map(self, fn): return self
    def merge(self, other):
        c = ImageCollection()
        c.dataset = self.dataset if "LANDSAT" in (self.dataset or "") else other.dataset
        return c
    def size(self): return _Value(self._size())
    def median(self): return Image(spec=f"{self.dataset}:median")
    def mean(self): return Image(spec=f"{self.dataset}:mean")
    def sum(self): return Image(spec=f"{self.dataset}:sum")
    def qualityMosaic(self, band): return Image(spec=f"{self.dataset}:qmosaic")
    def toList(self, n): return _Value([])


class FeatureCollection:
    def __init__(self, items=None):
        self.items = items or []
    def geometry(self):
        return Geometry("fc_geometry")
    def filterBounds(self, buf):
        fc = FeatureCollection()
        fc._filtered = True
        return fc
    def size(self):
        return _Value(FILTERBOUNDS_COUNT if getattr(self, "_filtered", False)
                      else len(self.items))
    def aggregate_array(self, prop):
        return _Value([])


def Initialize(*a, **k):
    return None
