"""
End-to-end assembly test: run engine.analyse() over the mock Earth Engine
backend and assert the whole pipeline produces a well-formed, JSON-serialisable
record with the integrity guarantees intact.

This is the test that covers the PLUMBING the pure-logic tests cannot: function
signatures, reduceRegion keys, the nutrition/climate wiring, the command-area
resolution path, and that nothing raises on a full run. It does NOT assert the
numbers are physically correct - the mock returns synthetic values - only that
the pipeline is wired correctly end to end.
"""

import json
import os


def _canal(name, coords):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": "LineString", "coordinates": coords}}


def _polygon(canal_name):
    return {"type": "Feature", "properties": {"canal": canal_name},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[35.7, 15.2], [35.9, 15.2],
                                          [35.9, 15.4], [35.7, 15.4],
                                          [35.7, 15.2]]]}}


CANAL_FC = {"type": "FeatureCollection", "features": [
    _canal("Canal 1", [[35.70, 15.20], [35.74, 15.24], [35.78, 15.28],
                       [35.82, 15.32], [35.86, 15.36], [35.90, 15.40]]),
    _canal("Canal 2", [[35.60, 15.10], [35.64, 15.14], [35.68, 15.18],
                       [35.72, 15.22], [35.76, 15.26], [35.80, 15.30]]),
]}
COMMAND_FC = {"type": "FeatureCollection",
              "features": [_polygon("Canal 1"), _polygon("Canal 2")]}


def test_full_run_offline(ee_env, tmp_path):
    import engine
    out = os.path.join(tmp_path, "results.json")
    results = engine.analyse(CANAL_FC, COMMAND_FC, season=2022, out_json=out,
                             crop="sorghum")

    # top-level shape
    assert results["tool"].startswith("Sudan Irrigation")
    assert results["command_geometry_supplied"] is True
    assert len(results["canals"]) == 2

    for canal in results["canals"]:
        # command area was matched to a real polygon, and said so (defect 1)
        assert canal["command_area_provenance"]["command_area_source"].startswith("REAL")

        # canal water: OK, with a value and full provenance (integrity rule 7)
        cw = canal["canal_water"]
        assert cw["status"] == "OK"
        assert cw["value"] is not None
        assert cw["provenance"]["sensor"].startswith("Sentinel-1")
        assert cw["provenance"]["observed_fraction"] is not None

        # head-tail equity: detected, with a CI, and the attribution caveat
        eq = canal["head_tail_equity"]
        assert eq["status"] == "OK"
        assert "head_tail_gap" in eq
        assert eq["head_tail_gap_ci95"] is not None
        assert "attributes nothing" in eq["attribution_caveat"]
        assert len(eq["reaches"]) >= 3

        # irrigated extent: Otsu split with a bimodality note in provenance
        ext = canal["irrigated_extent"]
        assert ext["status"] == "OK"
        assert "bimodality" in ext["provenance"]["notes"]

        # nutrition + climate were wired in (present, whatever their status)
        assert "nutrition" in canal
        assert "climate" in canal

    # regional context present and fenced
    assert "GRACE" in results["regional_context"]["note"]

    # the file was actually written and is valid JSON
    with open(out, encoding="utf-8") as fh:
        reloaded = json.load(fh)
    assert reloaded["season"]["start"] == "2022-07-01"


def test_field_condition_withholds_stress_without_a_reference(ee_env):
    # Bug guard: with no reference geometry there must be NO relative threshold
    # and NO stress verdict - the vigour VALUE is still reported.
    import engine
    field = engine.ee.Geometry({"type": "Polygon", "coordinates": []})
    out = engine.field_condition(field, "2022-07-01", "2023-03-31")
    vig = out["indicators"]["vigour"]
    assert vig["status"] == "OK"
    assert vig["value"] is not None
    assert vig["threshold"] is None            # no reference -> no threshold
    assert out["context"]["reading_status"] == "NOT AVAILABLE"


def test_field_condition_offers_verdict_with_a_reference(ee_env):
    import engine
    field = engine.ee.Geometry({"type": "Polygon", "coordinates": []})
    ref = engine.ee.Geometry({"type": "Polygon", "coordinates": [1]})   # different spec
    out = engine.field_condition(field, "2022-07-01", "2023-03-31", reference_geom=ref)
    vig = out["indicators"]["vigour"]
    assert vig["threshold"] is not None        # reference -> a real threshold
    # a verdict is offered (OK), whatever its direction
    assert out["context"]["reading_status"] == "OK"


def test_missing_command_areas_are_flagged_synthetic(ee_env, tmp_path):
    import engine
    out = os.path.join(tmp_path, "r2.json")
    results = engine.analyse(CANAL_FC, None, season=2022, out_json=out,
                             crop="sorghum")
    for canal in results["canals"]:
        src = canal["command_area_provenance"]["command_area_source"]
        assert src.startswith("SYNTHETIC")
