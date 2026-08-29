"""
Getting canal centrelines into the engine, and refusing the ones that would
produce confident wrong answers.

TWO ENTRY POINTS
----------------
    fetch     pull waterway=canal/ditch/drain from OpenStreetMap for a bounding
              box and write engine-ready GeoJSON
    validate  check ANY canal GeoJSON - fetched, traced by hand in QGIS, or
              supplied by a scheme authority - against what the engine actually
              requires

Validate is the more important of the two. Most real canal geometry in Sudan
will be digitised by hand over sub-metre imagery, because a Gezira minor canal
is 5-15 m wide and a 10 m water-frequency raster cannot see it. Hand digitising
is the right method and it produces exactly the defects this checker looks for.

WHAT IT REFUSES, AND WHY EACH ONE MATTERS
-----------------------------------------
NO DIRECTION
    The head-to-tail gap is signed. A LineString records nothing about which end
    the water enters, and a tracer's mouse direction is arbitrary. Without a
    `vertex_order` property or an `offtake` coordinate the engine marks the gap
    unverified; this checker escalates that to an error at ingest, where it is
    cheap to fix, rather than letting it travel into a report.

TOO FEW VERTICES
    The equity fit splits the line into reaches. A two-vertex line has one
    segment, cannot be split, and yields no slope. A straight canal drawn with
    two points is geometrically correct and analytically useless.

DUPLICATE NAMES
    Command areas are matched to canals by name. Two canals called "Minor 7"
    silently merge their command areas, and the resulting equity figure belongs
    to neither.

ZERO-LENGTH OR REPEATED VERTICES
    Produce degenerate reach buffers and NaN reductions downstream.

SUSPICIOUSLY LONG
    A single LineString hundreds of kilometres long is usually a tracing slip -
    two unrelated canals joined, or a stray vertex at the origin. It is reported
    rather than dropped, because a genuine main canal can be that long.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not guess direction from elevation. In Gezira the head-to-tail fall is
centimetres per kilometre, well inside SRTM's vertical noise, so a DEM cannot
separate the ends and would only dress an assumption up as a measurement.

RUN
---
    python canal_geometry.py fetch --bbox 32.9 14.2 33.4 14.7 \\
        --out gezira_canals.geojson
    python canal_geometry.py validate --canal gezira_canals.geojson \\
        --command-areas gezira_commands.geojson
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Optional

# Overpass is queried only by `fetch`. `validate` is pure and needs no network,
# so a machine with no internet can still check hand-traced geometry.
try:
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode
except ImportError:                                          # pragma: no cover
    urlopen = None


OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Engine requirements, each declared with the reason it exists.
MIN_VERTICES = 4          # 3 reaches minimum for a slope fit, so >= 4 points
MIN_LENGTH_M = 200.0      # shorter than this is not a canal reach worth splitting
LONG_CANAL_WARN_M = 200_000.0
EARTH_R_M = 6371000.0

WATERWAY_TAGS = ("canal", "ditch", "drain")


# ==============================================================================
# PURE GEOMETRY
# ==============================================================================

def _seg_len_m(a, b) -> float:
    """Equirectangular segment length. Adequate for a length sanity check over a
    canal; not a geodesic and never reported as a survey measurement."""
    lon1, lat1 = float(a[0]), float(a[1])
    lon2, lat2 = float(b[0]), float(b[1])
    mlat = math.radians((lat1 + lat2) / 2.0)
    dx = math.radians(lon2 - lon1) * math.cos(mlat)
    dy = math.radians(lat2 - lat1)
    return math.hypot(dx, dy) * EARTH_R_M


def line_length_m(coords) -> float:
    if not coords or len(coords) < 2:
        return 0.0
    return sum(_seg_len_m(a, b) for a, b in zip(coords, coords[1:]))


def repeated_vertices(coords) -> int:
    """Count consecutive identical vertices - a common artefact of snapping
    while digitising, and a source of zero-length reach buffers."""
    if not coords:
        return 0
    return sum(1 for a, b in zip(coords, coords[1:])
               if float(a[0]) == float(b[0]) and float(a[1]) == float(b[1]))


# ==============================================================================
# VALIDATION
# ==============================================================================

def validate_canal_feature(feature: dict, index: int = 0) -> dict:
    """
    Check one canal feature. Returns
    {"name", "errors": [...], "warnings": [...], "info": {...}}.

    Errors block a run. Warnings do not, but they are the things that make a
    figure hard to defend later, so they are printed in full rather than
    counted.
    """
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry", {}) or {}
    name = props.get("name") or f"(unnamed feature {index})"
    errors, warnings, info = [], [], {}

    gtype = geom.get("type")
    coords = geom.get("coordinates")

    if gtype == "MultiLineString":
        errors.append(
            "geometry is a MultiLineString. The engine splits ONE line into "
            "reaches head to tail; a multi-part geometry has no unambiguous "
            "order. Split it into separate named canals, or merge the parts "
            "into a single ordered LineString.")
        return {"name": name, "errors": errors, "warnings": warnings, "info": info}
    if gtype != "LineString":
        errors.append(f"geometry type is {gtype!r}; the engine needs a LineString")
        return {"name": name, "errors": errors, "warnings": warnings, "info": info}
    if not coords:
        errors.append("geometry has no coordinates")
        return {"name": name, "errors": errors, "warnings": warnings, "info": info}

    n = len(coords)
    info["vertices"] = n
    if n < MIN_VERTICES:
        errors.append(
            f"{n} vertices. The equity fit needs at least three reaches, so at "
            f"least {MIN_VERTICES} vertices. A straight canal drawn with two "
            "points is geometrically correct and analytically useless - add "
            "intermediate vertices along its actual course.")

    length = line_length_m(coords)
    info["length_m"] = round(length, 1)
    if length < MIN_LENGTH_M:
        errors.append(f"length {length:.0f} m is below the {MIN_LENGTH_M:.0f} m "
                      "minimum; this is not a splittable canal reach")
    if length > LONG_CANAL_WARN_M:
        warnings.append(
            f"length {length / 1000:.0f} km is unusually long for one canal. "
            "This is often two unrelated canals joined while tracing, or a "
            "stray vertex left near the origin. Check the ends.")

    dupes = repeated_vertices(coords)
    if dupes:
        warnings.append(f"{dupes} repeated consecutive vertices; these produce "
                        "degenerate reach buffers and should be removed")

    # coordinates plausibly in Sudan, and in lon/lat order
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    if any(abs(v) > 180 for v in lons) or any(abs(v) > 90 for v in lats):
        errors.append("coordinates are out of lon/lat range; the file may be in "
                      "a projected CRS. Reproject to EPSG:4326.")
    elif not (21 <= min(lons) and max(lons) <= 39 and
              3 <= min(lats) and max(lats) <= 23):
        warnings.append(
            f"coordinates (lon {min(lons):.2f}..{max(lons):.2f}, "
            f"lat {min(lats):.2f}..{max(lats):.2f}) fall outside Sudan. If this "
            "is deliberate, ignore; if not, the axes may be swapped.")

    # THE ONE THAT MATTERS MOST
    vo = props.get("vertex_order")
    offtake = props.get("offtake")
    if not vo and not offtake:
        errors.append(
            "no 'vertex_order' property and no 'offtake' coordinate. The "
            "head-to-tail gap is SIGNED, and nothing in a LineString records "
            "which end the water enters - reverse the line and the same "
            "imagery reports the head as starved instead of the tail. Add "
            "\"vertex_order\": \"head_first\" or \"tail_first\", or "
            "\"offtake\": [lon, lat] for the junction with the parent canal.")
    else:
        info["direction_source"] = "vertex_order" if vo else "offtake"
        if vo and str(vo).strip().lower().replace("-", "_") not in (
                "head_first", "tail_first"):
            errors.append(f"vertex_order is {vo!r}; expected 'head_first' or "
                          "'tail_first'")
        if offtake and (not isinstance(offtake, (list, tuple))
                        or len(offtake) != 2):
            errors.append(f"offtake is {offtake!r}; expected [lon, lat]")

    if not props.get("name"):
        errors.append("no 'name' property. Command areas are matched to canals "
                      "by name; an unnamed canal cannot be matched and falls "
                      "back to an arbitrary buffer.")

    if not props.get("width_m"):
        warnings.append(
            "no 'width_m' property. Without it the engine cannot say whether "
            "this canal is resolvable at 10 m radar, so its water figures "
            "carry no reliability qualifier. A Gezira minor at 5-15 m is "
            "below reliable detection and its readings should say so.")

    return {"name": name, "errors": errors, "warnings": warnings, "info": info}


def validate_collection(canal_fc: dict,
                        command_fc: Optional[dict] = None) -> dict:
    """Validate a whole canal FeatureCollection, plus cross-feature checks that
    a single feature cannot see."""
    feats = canal_fc.get("features", []) or []
    results = [validate_canal_feature(f, i) for i, f in enumerate(feats, 1)]

    collection_errors, collection_warnings = [], []
    if not feats:
        collection_errors.append("no features in the canal file")

    names = [(f.get("properties", {}) or {}).get("name") for f in feats]
    seen, dupes = set(), set()
    for nm in names:
        if nm and nm in seen:
            dupes.add(nm)
        seen.add(nm)
    if dupes:
        collection_errors.append(
            f"duplicate canal names: {sorted(dupes)}. Command areas are matched "
            "by name, so duplicates silently merge two canals' command areas "
            "and the resulting equity figure belongs to neither.")

    if command_fc:
        cmd_refs = {str((cf.get("properties", {}) or {}).get("canal")
                        or (cf.get("properties", {}) or {}).get("canal_name")
                        or (cf.get("properties", {}) or {}).get("name") or "")
                    .strip().lower()
                    for cf in command_fc.get("features", [])}
        unmatched = [nm for nm in names
                     if nm and nm.strip().lower() not in cmd_refs]
        if unmatched:
            collection_warnings.append(
                f"{len(unmatched)} canal(s) have no command-area polygon matched "
                f"by name: {unmatched[:5]}{' ...' if len(unmatched) > 5 else ''}. "
                "These fall back to an arbitrary buffer, which is recorded as "
                "SYNTHETIC in every affected figure's provenance.")
    else:
        collection_warnings.append(
            "no command-area file supplied. Every canal will use the arbitrary "
            "buffer fallback, and the field layer will withhold every stress "
            "verdict for want of a reference population.")

    n_err = sum(len(r["errors"]) for r in results) + len(collection_errors)
    n_warn = sum(len(r["warnings"]) for r in results) + len(collection_warnings)
    return {
        "ok": n_err == 0,
        "n_canals": len(feats),
        "n_errors": n_err,
        "n_warnings": n_warn,
        "per_canal": results,
        "collection_errors": collection_errors,
        "collection_warnings": collection_warnings,
    }


def print_report(report: dict) -> None:
    print("=" * 72)
    print(f"Canal geometry check: {report['n_canals']} canal(s), "
          f"{report['n_errors']} error(s), {report['n_warnings']} warning(s)")
    print("=" * 72)
    for e in report["collection_errors"]:
        print(f"ERROR   [collection] {e}")
    for w in report["collection_warnings"]:
        print(f"WARN    [collection] {w}")
    for r in report["per_canal"]:
        if not r["errors"] and not r["warnings"]:
            info = r["info"]
            print(f"OK      {r['name']}  "
                  f"({info.get('vertices')} vertices, "
                  f"{info.get('length_m', 0) / 1000:.1f} km, "
                  f"direction from {info.get('direction_source')})")
            continue
        print(f"\n--- {r['name']}")
        for e in r["errors"]:
            print(f"  ERROR {e}")
        for w in r["warnings"]:
            print(f"  WARN  {w}")
    print()
    if report["ok"]:
        print("PASS - this geometry can be run through the engine.")
    else:
        print("FAIL - fix the errors above before running the engine. Every one "
              "of them would otherwise become a number in a report that nobody "
              "could defend.")


# ==============================================================================
# OPENSTREETMAP FETCH
# ==============================================================================

def build_overpass_query(bbox, tags=WATERWAY_TAGS, timeout: int = 180) -> str:
    """bbox is (west, south, east, north). Overpass wants (south, west, north, east)."""
    w, s, e, n = bbox
    clauses = "\n".join(
        f'  way["waterway"="{t}"]({s},{w},{n},{e});' for t in tags)
    return f"[out:json][timeout:{timeout}];\n(\n{clauses}\n);\nout geom;"


def fetch_osm_canals(bbox, tags=WATERWAY_TAGS, url: str = OVERPASS_URL,
                     timeout: int = 180) -> dict:
    """
    Pull canal ways from OpenStreetMap and convert them to a canal
    FeatureCollection.

    HONEST NOTE ON COVERAGE
    OSM's canal coverage in Sudan is partial and uneven. Main and major canals
    are often mapped; minor canals usually are not, and field channels never
    are. An empty or thin result is a statement about OSM, not about the
    scheme - and the fallback is hand digitising over sub-metre imagery, which
    is the expected route for minor canals anyway.

    Every fetched canal is written WITHOUT a direction property, because OSM way
    direction is a mapping artefact and does not record which end the water
    enters. The validator will then refuse them until a person supplies it. That
    refusal is intentional: an inherited arbitrary direction is worse than an
    absent one, because it looks like information.
    """
    if urlopen is None:                                      # pragma: no cover
        raise RuntimeError("urllib unavailable")
    query = build_overpass_query(bbox, tags, timeout)
    req = Request(url, data=urlencode({"data": query}).encode(),
                  headers={"User-Agent": "sudan-irrigation-monitor/1.0"})
    with urlopen(req, timeout=timeout + 30) as resp:
        payload = json.loads(resp.read().decode())
    return osm_to_featurecollection(payload)


def osm_to_featurecollection(payload: dict) -> dict:
    """Convert an Overpass `out geom` response into canal features. Pure, so it
    is testable without a network."""
    feats = []
    for el in payload.get("elements", []):
        if el.get("type") != "way":
            continue
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        coords = [[p["lon"], p["lat"]] for p in geom]
        tags = el.get("tags", {}) or {}
        feats.append({
            "type": "Feature",
            "properties": {
                "name": tags.get("name") or f"osm_way_{el.get('id')}",
                "osm_id": el.get("id"),
                "waterway": tags.get("waterway"),
                "osm_name_ar": tags.get("name:ar"),
                "length_m": round(line_length_m(coords), 1),
                # Deliberately absent: vertex_order / offtake. See fetch docstring.
                "direction_note": (
                    "OSM way direction is a mapping artefact and does NOT record "
                    "which end water enters. Add 'vertex_order' or 'offtake' "
                    "before running the engine."),
            },
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    return {"type": "FeatureCollection", "features": feats}


# ==============================================================================
# CLI
# ==============================================================================

def main():
    p = argparse.ArgumentParser(
        description="Fetch canal centrelines from OSM, or validate any canal "
                    "GeoJSON against what the engine requires")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="pull waterway=canal/ditch/drain from OSM")
    f.add_argument("--bbox", nargs=4, type=float, required=True,
                   metavar=("W", "S", "E", "N"))
    f.add_argument("--out", required=True)
    f.add_argument("--tags", nargs="+", default=list(WATERWAY_TAGS))

    v = sub.add_parser("validate", help="check a canal GeoJSON")
    v.add_argument("--canal", required=True)
    v.add_argument("--command-areas", default=None)

    a = p.parse_args()

    if a.cmd == "fetch":
        print(f"Querying Overpass for {a.tags} in bbox {a.bbox} ...")
        fc = fetch_osm_canals(a.bbox, tags=tuple(a.tags))
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(fc, fh, indent=1, ensure_ascii=False)
        print(f"Wrote {len(fc['features'])} canal(s) to {a.out}")
        if not fc["features"]:
            print("\nNo canals found. OSM canal coverage in Sudan is partial: "
                  "main and major canals are often mapped, minor canals usually "
                  "are not. Hand digitising over sub-metre imagery is the "
                  "expected route for minor canals.")
            return
        print("\nNow add 'vertex_order' or 'offtake' to each canal - the engine "
              "will refuse them otherwise, because the head-to-tail gap is "
              "signed and OSM way direction does not record which end the water "
              "enters. Then run:")
        print(f"  python canal_geometry.py validate --canal {a.out}")
        return

    with open(a.canal, encoding="utf-8") as fh:
        canal_fc = json.load(fh)
    command_fc = None
    if a.command_areas:
        with open(a.command_areas, encoding="utf-8") as fh:
            command_fc = json.load(fh)
    report = validate_collection(canal_fc, command_fc)
    print_report(report)
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
