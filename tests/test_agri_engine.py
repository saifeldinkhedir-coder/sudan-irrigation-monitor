"""
The agriculture engine.

Two things are being pinned here. First, the separation: this engine must work
from field polygons alone, and must not reach into the network engine for
anything. Second, the ranking, which is the farmer-facing output most likely to
be misread as a score - it is an ordering, and an unmeasured field must not be
allowed to sink to the bottom of it as though it had been checked and found bad.
"""

import json

import agri_engine as ag


def _square(lon, lat, side_deg, name=None):
    return {
        "type": "Feature",
        "properties": {"name": name} if name else {},
        "geometry": {"type": "Polygon", "coordinates": [[
            [lon, lat], [lon + side_deg, lat],
            [lon + side_deg, lat + side_deg], [lon, lat + side_deg],
            [lon, lat]]]},
    }


FIELD = _square(33.10, 14.40, 0.004, "Field A")        # ~ 0.2 km2
HUGE = _square(33.10, 14.40, 0.30, "Whole district")   # far bigger than 3 km buffer


# --- the separation -----------------------------------------------------------

class TestSeparation:
    def test_it_does_not_import_the_network_engine(self):
        """
        The point of the split. If this engine ever imports engine.py it has
        re-acquired the canal-shaped input contract it exists to avoid.

        Parsed from the AST rather than grepped from the source: the first
        version of this test searched the text and tripped over the module's own
        docstring, which says "imports nothing from engine.py". A test that a
        comment can fail is testing prose, not behaviour.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(ag))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert "engine" not in imported
        assert "network" not in imported
        assert "attribution" not in imported

    def test_the_season_window_is_the_cropping_year_not_the_calendar(self):
        start, end = ag.season_window(2022)
        assert start == "2022-07-01"
        assert end == "2023-03-31"

    def test_no_fields_produces_no_report_rather_than_an_empty_farm(self, ee_env):
        import importlib
        importlib.reload(ag)
        out = ag.analyse_farm({"type": "FeatureCollection", "features": []},
                              2022, "unused.json")
        assert out["n_fields"] == 0
        assert out["fields"] == []


# --- the neighbourhood --------------------------------------------------------

class TestNeighbourhood:
    def test_a_normal_field_gets_a_buffer_reference(self, ee_env):
        import importlib
        importlib.reload(ag)
        geom = ee_env.Geometry(FIELD["geometry"])
        ref, prov = ag.neighbourhood_for(FIELD, geom)
        assert ref is not None
        assert prov["verdict_withheld"] is False
        assert prov["area_ratio"] > 10
        assert "NEIGHBOURHOOD" in prov["reference_source"]

    def test_a_field_bigger_than_its_buffer_is_refused_a_threshold(self, ee_env):
        import importlib
        importlib.reload(ag)
        geom = ee_env.Geometry(HUGE["geometry"])
        ref, prov = ag.neighbourhood_for(HUGE, geom)
        assert ref is None
        assert prov["verdict_withheld"] is True
        assert "too large" in prov["reference_source"]

    def test_the_buffer_width_is_declared_arbitrary(self, ee_env):
        import importlib
        importlib.reload(ag)
        geom = ee_env.Geometry(FIELD["geometry"])
        _, prov = ag.neighbourhood_for(FIELD, geom)
        assert "ARBITRARY" in prov["buffer_basis"]

    def test_geometry_without_area_yields_no_reference(self, ee_env):
        import importlib
        importlib.reload(ag)
        line = {"properties": {"name": "not a field"},
                "geometry": {"type": "LineString",
                             "coordinates": [[33.0, 14.4], [33.1, 14.5]]}}
        ref, prov = ag.neighbourhood_for(line, ee_env.Geometry(line["geometry"]))
        assert ref is None
        assert prov["verdict_withheld"] is True


# --- ranking: an ordering, not a score ----------------------------------------

def _field_record(name, vigour, threshold=None, moisture=None,
                  moisture_threshold=None, warmer=None, deficit=None,
                  vigour_status="OK"):
    rec = {"name": name, "crop_health": {"readings": {
        "vigour": {"status": vigour_status, "value": vigour,
                   "threshold": threshold,
                   "reason": None if vigour_status == "OK" else "no scenes"}}}}
    if moisture is not None:
        rec["crop_health"]["readings"]["canopy_moisture"] = {
            "status": "OK", "value": moisture, "threshold": moisture_threshold}
    if warmer is not None:
        rec["thermal_stress"] = {"status": "OK", "difference_c": warmer}
    if deficit is not None:
        rec["water_requirement"] = {"status": "OK",
                                    "irrigation_requirement_mm": deficit}
    return rec


class TestRanking:
    def test_a_field_below_its_threshold_comes_first(self):
        r = ag.rank_fields([
            _field_record("healthy", 0.62, threshold=0.30),
            _field_record("struggling", 0.21, threshold=0.30),
        ])
        assert r["ranked"][0]["name"] == "struggling"
        assert r["ranked"][0]["below_threshold"] is True
        assert r["ranked"][1]["below_threshold"] is False

    def test_the_drivers_say_why_a_field_is_ranked_where_it_is(self):
        r = ag.rank_fields([
            _field_record("F1", 0.21, threshold=0.30, moisture=0.05,
                          moisture_threshold=0.15, warmer=2.4, deficit=310.0)])
        drivers = r["ranked"][0]["drivers"]
        assert any("vigour below" in d for d in drivers)
        assert any("moisture below" in d for d in drivers)
        assert any("warmer" in d for d in drivers)
        assert any("mm of water" in d for d in drivers)

    def test_an_unmeasured_field_is_set_aside_not_ranked_last(self):
        """
        The failure this prevents: a field with no usable scenes sorting to the
        bottom of the list and being read as the worst field on the farm.
        Unmeasured is neither healthy nor sick.
        """
        r = ag.rank_fields([
            _field_record("measured", 0.55, threshold=0.30),
            _field_record("cloudy", None, vigour_status="NOT AVAILABLE"),
        ])
        assert [x["name"] for x in r["ranked"]] == ["measured"]
        assert r["unmeasured"][0]["name"] == "cloudy"
        assert "neither healthy nor sick" in r["unmeasured_note"]

    def test_no_health_score_is_invented(self):
        r = ag.rank_fields([_field_record("F1", 0.4, threshold=0.3)])
        assert "score" not in r["ranked"][0]
        assert "_sort" not in r["ranked"][0]
        assert "not a score" in r["basis"]

    def test_a_field_without_a_threshold_is_still_ranked_by_vigour(self):
        """No neighbourhood means no flag, but the values are real and
        comparable, so the ordering is still useful."""
        r = ag.rank_fields([
            _field_record("low", 0.20), _field_record("high", 0.60)])
        assert [x["name"] for x in r["ranked"]] == ["low", "high"]
        assert all(x["below_threshold"] is False for x in r["ranked"])

    def test_ranks_are_consecutive_from_one(self):
        r = ag.rank_fields([_field_record(f"F{i}", 0.1 * i, threshold=0.25)
                            for i in range(1, 6)])
        assert [x["rank"] for x in r["ranked"]] == [1, 2, 3, 4, 5]


# --- assembly -----------------------------------------------------------------

class TestFarmReport:
    def test_a_farm_report_runs_from_fields_alone(self, ee_env, tmp_path):
        import importlib
        importlib.reload(ag)
        fc = {"type": "FeatureCollection", "features": [
            _square(33.10, 14.40, 0.004, "Field A"),
            _square(33.12, 14.42, 0.004, "Field B")]}
        out = str(tmp_path / "farm.json")
        res = ag.analyse_farm(fc, 2022, out, crop="sorghum", with_series=False)

        assert res["n_fields"] == 2
        assert len(res["fields"]) == 2
        assert "ranking" in res
        assert "vigour" in res["fields"][0]["crop_health"]["readings"]
        with open(out, encoding="utf-8") as fh:
            assert json.load(fh)["n_fields"] == 2

    def test_the_report_carries_its_limitations(self, ee_env, tmp_path):
        import importlib
        importlib.reload(ag)
        fc = {"type": "FeatureCollection",
              "features": [_square(33.10, 14.40, 0.004, "Field A")]}
        res = ag.analyse_farm(fc, 2022, str(tmp_path / "f.json"),
                              with_series=False)
        joined = " ".join(res["limitations"])
        assert "NEEDED" in joined            # requirement vs delivery
        assert "Yield is refused" in joined
        assert "not a calibrated health score" in joined
        assert "not a soil test" in joined

    def test_every_sensor_named_in_the_report_is_scale_qualified(self, ee_env,
                                                                 tmp_path):
        import importlib
        importlib.reload(ag)
        fc = {"type": "FeatureCollection",
              "features": [_square(33.10, 14.40, 0.004, "Field A")]}
        res = ag.analyse_farm(fc, 2022, str(tmp_path / "f.json"),
                              with_series=False)
        sensors = res["sensors"]
        assert "100 m" in sensors["Landsat 8/9 thermal"]
        assert "28 km" in sensors["NOAA GFS"]
        assert "250 m" in sensors["OpenLandMap"]


# --- phenology ----------------------------------------------------------------

class TestPhenology:
    def _season(self):
        import math
        days = list(range(0, 270, 10))
        ndvi = [0.10 + 0.60 * math.exp(-((d - 140) / 45) ** 2) for d in days]
        return days, ndvi

    def test_a_clean_season_yields_greenup_peak_and_length(self):
        days, ndvi = self._season()
        p = ag.phenology(days, ndvi)
        assert p["status"] == "OK"
        assert p["greenup_day"] is not None
        assert p["peak_day"] == 140.0
        assert p["season_length_days"] > 0

    def test_too_few_scenes_is_refused_not_guessed(self):
        p = ag.phenology([0, 10, 20], [0.2, 0.6, 0.3])
        assert p["status"] == "NOT AVAILABLE"
        assert "usable scenes" in p["reason"]

    def test_a_flat_field_says_it_may_not_have_been_cropped(self):
        days = list(range(0, 270, 10))
        p = ag.phenology(days, [0.20] * len(days))
        assert p["status"] == "NOT AVAILABLE"
        assert "not a data failure" in p["reason"]
        assert "may simply not have been cropped" in p["reason"]

    def test_the_convention_is_declared_arbitrary(self):
        days, ndvi = self._season()
        assert "ARBITRARY" in ag.phenology(days, ndvi)["basis"]

    def test_cloud_gaps_are_admitted_in_the_basis(self):
        days, ndvi = self._season()
        assert "cloud gaps" in ag.phenology(days, ndvi)["basis"]


class TestSeriesOffsets:
    def test_dates_become_day_offsets_from_the_season_start(self):
        series = {"status": "OK",
                  "dates": ["2022-07-01", "2022-07-11", "2022-08-10"],
                  "ndvi": [0.2, 0.3, 0.5]}
        days, values = ag._series_day_offsets(series, "2022-07-01")
        assert days == [0, 10, 40]
        assert values == [0.2, 0.3, 0.5]

    def test_a_missing_series_yields_none_so_the_caller_falls_back(self):
        assert ag._series_day_offsets({}, "2022-07-01") == (None, None)
        assert ag._series_day_offsets(
            {"status": "NOT AVAILABLE"}, "2022-07-01") == (None, None)

    def test_one_usable_point_is_not_enough_for_an_integral(self):
        series = {"status": "OK", "dates": ["2022-07-01", "2022-07-11"],
                  "ndvi": [0.2, None]}
        assert ag._series_day_offsets(series, "2022-07-01") == (None, None)
