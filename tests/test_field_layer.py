"""
The field layer, and specifically the reference area its stress verdict depends
on.

field_condition already refused to give a verdict with no reference at all. The
subtler failure is a reference that EXISTS but is barely wider than the field:
the threshold is then set by the field's own pixels, the field can never fall
two robust sigma below a distribution it dominates, and "not stressed" comes out
looking exactly like a real observation. These tests pin that case to a refusal.
"""

import json
import os

import decision_logic as dl


def _square(lon, lat, side_deg, name=None, extra=None):
    """A lon/lat square as a GeoJSON Feature."""
    props = {"name": name} if name else {}
    if extra:
        props.update(extra)
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Polygon", "coordinates": [[
            [lon, lat], [lon + side_deg, lat],
            [lon + side_deg, lat + side_deg], [lon, lat + side_deg],
            [lon, lat]]]},
    }


FIELD = _square(33.10, 14.40, 0.005, "Field A")            # ~ 0.3 km2
BIG_COMMAND = _square(33.05, 14.35, 0.050, extra={"canal": "Minor 1"})  # ~ 30 km2
TIGHT_COMMAND = _square(33.10, 14.40, 0.006, extra={"canal": "Minor 1"})


# --- pure geometry helpers ----------------------------------------------------

def test_area_of_a_known_square_is_about_right():
    # 0.01 deg at 14.4 N: ~1.111 km north-south, ~1.077 km east-west.
    a = dl.geojson_area_m2(_square(33.0, 14.4, 0.01)["geometry"])
    assert 1.1e6 < a < 1.3e6


def test_holes_are_subtracted():
    solid = dl.geojson_area_m2(_square(33.0, 14.4, 0.02)["geometry"])
    with_hole = {"type": "Polygon", "coordinates": [
        _square(33.0, 14.4, 0.02)["geometry"]["coordinates"][0],
        _square(33.005, 14.405, 0.01)["geometry"]["coordinates"][0]]}
    assert dl.geojson_area_m2(with_hole) < solid


def test_a_linestring_has_no_area_rather_than_zero_area():
    assert dl.geojson_area_m2(
        {"type": "LineString", "coordinates": [[33.0, 14.4], [33.1, 14.4]]}) is None


def test_point_in_geometry():
    assert dl.point_in_geometry([33.102, 14.402], FIELD["geometry"])
    assert not dl.point_in_geometry([33.200, 14.402], FIELD["geometry"])


# --- the decision -------------------------------------------------------------

def test_a_wide_reference_is_accepted():
    r = dl.reference_adequate(3.0e5, 3.0e7)
    assert r["ok"] is True
    assert r["ratio"] == 100.0


def test_a_reference_barely_wider_than_the_field_is_refused():
    r = dl.reference_adequate(3.0e5, 4.0e5)
    assert r["ok"] is False
    assert "could never flag the field" in r["reason"]


def test_the_minimum_ratio_is_declared_arbitrary():
    assert "ARBITRARY" in dl.reference_adequate(1.0, 100.0)["basis"]


def test_zero_area_field_is_refused_not_divided_by():
    r = dl.reference_adequate(0.0, 1.0e7)
    assert r["ok"] is False
    assert r["ratio"] is None


# --- engine wiring ------------------------------------------------------------

def test_reference_resolution_prefers_the_containing_command_area(ee_env):
    import engine
    ref, prov = engine.resolve_field_reference(
        FIELD, {"type": "FeatureCollection", "features": [BIG_COMMAND]})
    assert ref is not None
    assert prov["reference_source"].startswith("REAL")
    assert prov["verdict_withheld"] is False
    assert prov["area_ratio"] > 10


def test_a_too_tight_command_area_falls_through_to_no_reference(ee_env):
    import engine
    ref, prov = engine.resolve_field_reference(
        FIELD, {"type": "FeatureCollection", "features": [TIGHT_COMMAND]})
    assert ref is None
    assert prov["verdict_withheld"] is True
    assert prov["rejected_candidates"]


def test_no_command_areas_means_no_reference_and_a_stated_reason(ee_env):
    import engine
    ref, prov = engine.resolve_field_reference(FIELD, None)
    assert ref is None
    assert prov["verdict_withheld"] is True
    assert "NOT AVAILABLE" in prov["reference_source"]


def test_analyse_runs_the_field_layer_and_records_it(ee_env, tmp_path):
    import engine
    canals = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"name": "Minor 1", "vertex_order": "head_first"},
        "geometry": {"type": "LineString", "coordinates": [
            [33.06, 14.36], [33.07, 14.37], [33.08, 14.38],
            [33.09, 14.39], [33.10, 14.40]]}}]}
    commands = {"type": "FeatureCollection", "features": [BIG_COMMAND]}
    fields = {"type": "FeatureCollection", "features": [FIELD]}
    out = str(tmp_path / "results.json")

    res = engine.analyse(canals, commands, 2022, out,
                         crop="sorghum", field_fc=fields)

    assert res["field_geometry_supplied"] is True
    assert len(res["fields"]) == 1
    f = res["fields"][0]
    assert f["name"] == "Field A"
    assert "vigour" in f["condition"]["indicators"]
    assert f["reference_provenance"]["verdict_withheld"] is False
    # and it survived the round-trip to disk
    with open(out, encoding="utf-8") as fh:
        assert json.load(fh)["fields"][0]["name"] == "Field A"


def test_analyse_without_fields_reports_an_empty_field_layer(ee_env, tmp_path):
    import engine
    canals = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"name": "Minor 1"},
        "geometry": {"type": "LineString", "coordinates": [
            [33.06, 14.36], [33.08, 14.38], [33.10, 14.40]]}}]}
    res = engine.analyse(canals, None, 2022, str(tmp_path / "r.json"))
    assert res["field_geometry_supplied"] is False
    assert res["fields"] == []


def test_field_values_are_reported_even_when_the_verdict_is_withheld(ee_env, tmp_path):
    """The distinction the whole layer rests on: 'not measured' must not look
    like 'measured and fine'."""
    import engine
    canals = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"name": "Minor 1"},
        "geometry": {"type": "LineString", "coordinates": [
            [33.06, 14.36], [33.08, 14.38], [33.10, 14.40]]}}]}
    fields = {"type": "FeatureCollection", "features": [FIELD]}
    res = engine.analyse(canals, None, 2022, str(tmp_path / "r.json"),
                         field_fc=fields)
    f = res["fields"][0]
    assert f["reference_provenance"]["verdict_withheld"] is True
    vig = f["condition"]["indicators"]["vigour"]
    assert vig["value"] is not None          # the measurement is still given
    assert vig["threshold"] is None          # but no threshold was invented
