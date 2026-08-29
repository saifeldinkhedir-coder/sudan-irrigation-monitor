"""
Command-line entry point for the Sudan Irrigation & Agriculture Monitor.

    python cli.py --canal canals.geojson --command-areas commands.geojson \
                  --fields fields.geojson \
                  --season 2024 --crop sorghum --out results.json

--command-areas is optional but strongly recommended: without real command-area
polygons the engine falls back to an arbitrary buffer around each canal and says
so in every affected number's provenance.

--fields turns on the FIELD layer (per-field vigour, canopy moisture, thermal
stress, rainfall context and red-edge nutrition). It is off without polygons
because there is no honest way to invent a field boundary from a canal line.
The field layer also leans on --command-areas: a field's stress threshold is
derived from the surrounding population, and with no command polygons there is
no population, so values are reported and the stress VERDICT is withheld.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import engine


def main():
    p = argparse.ArgumentParser(
        description="Multi-sensor irrigation network and field monitoring")
    p.add_argument("--canal", required=True,
                   help="GeoJSON of canal centrelines (LineString features)")
    p.add_argument("--command-areas", default=None,
                   help="GeoJSON of command-area polygons (recommended)")
    p.add_argument("--fields", default=None,
                   help="GeoJSON of field polygons; enables the field layer")
    p.add_argument("--rangeland", default=None,
                   help="GeoJSON of rangeland area polygons; enables the "
                        "rangeland layer (carries a conflict-sensitivity note "
                        "on every result and refuses areas named with claim "
                        "language)")
    p.add_argument("--season", type=int, default=2024,
                   help="season start year; window runs July to March")
    p.add_argument("--crop", default="default",
                   help="crop for GDD/heat-stress bases (wheat|sorghum|cotton|"
                        "groundnut|default)")
    p.add_argument("--no-nutrition-climate", action="store_true",
                   help="run only the network/field layers")
    p.add_argument("--out", default="irrigation_results.json")
    a = p.parse_args()

    if not os.path.exists(a.canal):
        print(f"ABORT: {a.canal} not found.")
        sys.exit(1)
    with open(a.canal, encoding="utf-8") as fh:
        canal_fc = json.load(fh)

    command_fc = None
    if a.command_areas:
        if not os.path.exists(a.command_areas):
            print(f"ABORT: {a.command_areas} not found.")
            sys.exit(1)
        with open(a.command_areas, encoding="utf-8") as fh:
            command_fc = json.load(fh)

    field_fc = None
    if a.fields:
        if not os.path.exists(a.fields):
            print(f"ABORT: {a.fields} not found.")
            sys.exit(1)
        with open(a.fields, encoding="utf-8") as fh:
            field_fc = json.load(fh)
        if not a.command_areas:
            print("NOTE: --fields without --command-areas. Per-field values will "
                  "be reported but every stress VERDICT will be withheld, "
                  "because there is no surrounding population to derive a "
                  "threshold from.")

    rangeland_fc = None
    if a.rangeland:
        if not os.path.exists(a.rangeland):
            print(f"ABORT: {a.rangeland} not found.")
            sys.exit(1)
        with open(a.rangeland, encoding="utf-8") as fh:
            rangeland_fc = json.load(fh)

    engine.analyse(canal_fc, command_fc, a.season, a.out, crop=a.crop,
                   nutrition_climate=not a.no_nutrition_climate,
                   field_fc=field_fc, rangeland_fc=rangeland_fc)


if __name__ == "__main__":
    main()
