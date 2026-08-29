"""
Test for defect #1: command-area geometry must actually be used when supplied,
and the provenance must say truthfully whether the area was real or a synthetic
fallback buffer.

This is the bug that most directly violated integrity rule 1 - the first draft
parsed --command-areas and then analysed a buffer regardless, so a real polygon
was silently discarded behind a number that looked identical either way.

Runs over the shared mock Earth Engine backend (the `ee_env` fixture). We are
testing the branching logic that decides which geometry wins and what the
provenance records - not Earth Engine itself.
"""

CANAL = {"type": "Feature",
         "properties": {"name": "Canal 7"},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}


def _command_fc(*props):
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": p,
                          "geometry": {"type": "Polygon", "coordinates": []}}
                         for p in props]}


def test_no_command_fc_is_synthetic_and_labelled_so(ee_env):
    import engine
    ee_env.FILTERBOUNDS_COUNT = 0
    _, prov = engine.resolve_command_area(
        CANAL, engine.ee.Geometry(CANAL["geometry"]), command_fc=None)
    assert prov["command_area_source"].startswith("SYNTHETIC")
    assert "buffer_half_width_m" in prov


def test_matched_by_name_is_real(ee_env):
    import engine
    ee_env.FILTERBOUNDS_COUNT = 0
    fc = _command_fc({"canal": "Canal 7"}, {"canal": "Canal 9"})
    _, prov = engine.resolve_command_area(
        CANAL, engine.ee.Geometry(CANAL["geometry"]), fc)
    assert prov["command_area_source"].startswith("REAL")
    assert "matched by name" in prov["command_area_source"]
    assert prov["matched_polygons"] == 1


def test_matched_by_name_is_case_insensitive(ee_env):
    import engine
    fc = _command_fc({"name": "canal 7"})
    _, prov = engine.resolve_command_area(
        CANAL, engine.ee.Geometry(CANAL["geometry"]), fc)
    assert prov["command_area_source"].startswith("REAL")


def test_spatial_fallback_when_no_name_match_but_intersects(ee_env):
    import engine
    ee_env.FILTERBOUNDS_COUNT = 2          # two polygons intersect the buffer
    fc = _command_fc({"canal": "Something Else"})
    _, prov = engine.resolve_command_area(
        CANAL, engine.ee.Geometry(CANAL["geometry"]), fc)
    assert prov["command_area_source"].startswith("REAL")
    assert "intersecting" in prov["command_area_source"]
    assert prov["matched_polygons"] == 2


def test_synthetic_when_polygons_supplied_but_none_match_or_intersect(ee_env):
    import engine
    ee_env.FILTERBOUNDS_COUNT = 0          # nothing matches, nothing intersects
    fc = _command_fc({"canal": "Elsewhere"})
    _, prov = engine.resolve_command_area(
        CANAL, engine.ee.Geometry(CANAL["geometry"]), fc)
    assert prov["command_area_source"].startswith("SYNTHETIC")
