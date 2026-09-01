"""
Command-line entry point for the AGRICULTURE engine.

    export EE_PROJECT=your-ee-project
    python src/farm_cli.py --fields my_fields.geojson --season 2022 \
        --crop sorghum --out farm_report.json

This is the farm product. It needs field polygons and nothing else - no canal
centrelines, no command areas, no scheme. If you want the irrigation network
layer (canal water, continuity, head-to-tail equity), that is a different
engine with a different input contract: use src/cli.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import agri_engine


def main():
    p = argparse.ArgumentParser(
        description="Farm monitoring from field polygons: crop health, "
                    "moisture, thermal stress, nutrition, water requirement, "
                    "climate and advisory")
    p.add_argument("--fields", required=True,
                   help="GeoJSON of field polygons (Polygon features)")
    p.add_argument("--season", type=int, default=2022,
                   help="season start year; window runs July to March")
    p.add_argument("--crop", default="default",
                   help="the DEFAULT crop for fields that do not declare one. "
                        "Any field carrying a `crop` property overrides it. "
                        "See src/crops.py for the list.")
    p.add_argument("--no-series", action="store_true",
                   help="skip the per-scene time series (faster)")
    p.add_argument("--restart", action="store_true",
                   help="ignore any checkpoint from an interrupted run and "
                        "start over. Without it, a run that died part way "
                        "resumes - but only if the boundaries, season, crop "
                        "and series setting are unchanged.")
    p.add_argument("--no-resume", action="store_true",
                   help="do not write a checkpoint at all")
    p.add_argument("--observations", default="observations.db",
                   help="the scouting database. It is the ONLY source that "
                        "can name a disease; without it the disease layer "
                        "stops at an unnamed anomaly.")
    p.add_argument("--out", default="farm_report.json")
    a = p.parse_args()

    if not os.path.exists(a.fields):
        print(f"ABORT: {a.fields} not found.")
        sys.exit(1)
    with open(a.fields, encoding="utf-8") as fh:
        field_fc = json.load(fh)

    n = len(field_fc.get("features", []))
    if n == 0:
        print("ABORT: the field file contains no features. There is no honest "
              "way to invent a field boundary.")
        sys.exit(1)

    agri_engine.analyse_farm(field_fc, a.season, a.out, crop=a.crop,
                             with_series=not a.no_series,
                             observations_db=a.observations,
                             resume=not a.no_resume, restart=a.restart)


if __name__ == "__main__":
    main()
