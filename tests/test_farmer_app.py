"""
Farmer app display logic.

Almost every test here is about the third colour. A map is more persuasive than
a table and nobody reads a colour sceptically, so the single most damaging thing
this app could do is draw an unmeasured field in the same green as a healthy one.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "farmer_app"))
import view as D


def _rec(name, vigour=0.5, threshold=0.3, status="OK", reason=None):
    return {"name": name, "crop_health": {"readings": {
        "vigour": {"status": status, "value": vigour, "threshold": threshold,
                   "reason": reason, "sensor": "Sentinel-2 median",
                   "scale_m": 10}}}}


def _square(lon, lat, name):
    s = 0.005
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon, lat], [lon + s, lat], [lon + s, lat + s],
                [lon, lat + s], [lon, lat]]]}}


# --- the third colour ---------------------------------------------------------

class TestStatusColours:
    def test_below_threshold_is_attention(self):
        s = D.field_status(_rec("F", vigour=0.2, threshold=0.3))
        assert s["status"] == "attention"
        assert s["colour"] == D.COLOUR_ATTENTION

    def test_above_threshold_is_ok(self):
        s = D.field_status(_rec("F", vigour=0.6, threshold=0.3))
        assert s["status"] == "ok"

    def test_unmeasured_is_neither_green_nor_red(self):
        s = D.field_status(_rec("F", vigour=None, status="NOT AVAILABLE",
                                reason="no scenes"))
        assert s["status"] == "unmeasured"
        assert s["colour"] == D.COLOUR_UNMEASURED
        assert s["colour"] != D.COLOUR_OK
        assert s["colour"] != D.COLOUR_ATTENTION

    def test_the_four_colours_are_all_distinct(self):
        colours = [D.COLOUR_ATTENTION, D.COLOUR_WATCH, D.COLOUR_OK,
                   D.COLOUR_UNMEASURED]
        assert len({tuple(c) for c in colours}) == 4

    def test_the_legend_spells_out_what_grey_means(self):
        entry = next(e for e in D.LEGEND if e[0] == "not measured")
        assert "NOT a healthy field" in entry[2]

    def test_a_field_with_no_threshold_is_not_forced_into_a_verdict(self):
        s = D.field_status(_rec("F", vigour=0.4, threshold=None))
        assert s["status"] in ("ok", "watch")
        assert "not below the neighbourhood threshold" in s["why"] \
            or "lowest third" in s["why"]

    def test_the_watch_band_says_it_is_a_within_farm_comparison(self):
        s = D.field_status(_rec("F", vigour=0.20, threshold=None),
                           farm_vigours=[0.20, 0.45, 0.60, 0.70])
        assert s["status"] == "watch"
        assert "within the farm, not" in s["why"]

    def test_a_two_field_farm_has_no_watch_band(self):
        """Ranking the lowest third of two fields is not a comparison."""
        s = D.field_status(_rec("F", vigour=0.20, threshold=None),
                           farm_vigours=[0.20, 0.60])
        assert s["status"] == "ok"


# --- the map join -------------------------------------------------------------

class TestMapFeatures:
    def test_polygons_are_joined_to_results_by_name(self):
        report = {"fields": [_rec("Field A", 0.2, 0.3), _rec("Field B", 0.6, 0.3)]}
        fc = {"features": [_square(33.0, 14.4, "Field A"),
                           _square(33.1, 14.5, "Field B")]}
        feats = D.map_features(report, fc)
        assert [f["name"] for f in feats] == ["Field A", "Field B"]
        assert feats[0]["status"] == "attention"
        assert feats[1]["status"] == "ok"

    def test_a_polygon_with_no_result_is_drawn_grey_not_dropped(self):
        """A field silently missing from the map reads as a field the farmer
        does not have."""
        report = {"fields": [_rec("Field A")]}
        fc = {"features": [_square(33.0, 14.4, "Field A"),
                           _square(33.1, 14.5, "Field Z")]}
        feats = D.map_features(report, fc)
        assert len(feats) == 2
        z = next(f for f in feats if f["name"] == "Field Z")
        assert z["status"] == "unmeasured"
        assert "not in the report" in z["why"]

    def test_non_polygon_geometry_is_skipped(self):
        fc = {"features": [{"type": "Feature", "properties": {"name": "line"},
                            "geometry": {"type": "LineString",
                                         "coordinates": [[33, 14], [33.1, 14.1]]}}]}
        assert D.map_features({"fields": []}, fc) == []

    def test_every_feature_carries_a_reason_for_its_colour(self):
        report = {"fields": [_rec("Field A", 0.2, 0.3)]}
        fc = {"features": [_square(33.0, 14.4, "Field A")]}
        assert D.map_features(report, fc)[0]["why"]


# --- the variables table ------------------------------------------------------

class TestVariablesTable:
    def _full(self):
        return {
            "name": "Field 2",
            "crop_health": {"readings": {
                "vigour": {"status": "OK", "value": 0.219, "threshold": 0.30,
                           "sensor": "Sentinel-2 median", "scale_m": 10},
                "canopy_moisture": {"status": "OK", "value": 0.05,
                                    "threshold": 0.12,
                                    "sensor": "Sentinel-2 median", "scale_m": 10},
                "greenness": {"status": "NOT AVAILABLE", "reason": "no pixels"}}},
            "thermal_stress": {"status": "OK", "value": 42.41,
                               "neighbourhood_c": 39.0, "difference_c": 3.41,
                               "reading": "warmer than the surrounding land",
                               "sensor": "Landsat 8/9 ST_B10", "scale_m": 100},
            "rainfall": {"season_mm": 228.0, "last_14d_mm": 0.0,
                         "sensor": "CHIRPS daily"},
            "water_requirement": {"status": "OK", "et0_mm": 1792.3,
                                  "et0_mm_per_day": 6.6, "etc_mm": 385.3,
                                  "kcb": 0.215},
            "climate": {"growing_degree_days": 5278.5, "gdd_base_c": 10.0,
                        "heat_stress_days": 80, "heat_stress_threshold_c": 38.0,
                        "dry_spells": {"longest_dry_spell_days": 173,
                                       "threshold_days": 10, "flagged": True},
                        "season_vs_history": {"this_season_mm": 228.0,
                                              "verdict": "drier than usual"}},
            "soil": {"status": "OK", "texture": "clay"},
        }

    def test_every_variable_appears(self):
        labels = {r["variable"] for r in D.variables_table(self._full())}
        for expected in ("Vigour (NDVI)", "Canopy moisture (NDMI)",
                         "Surface temperature", "Rainfall, season",
                         "Crop water NEEDED (ETc)", "Growing degree days",
                         "Heat-stress days", "Longest dry spell",
                         "Soil texture"):
            assert expected in labels

    def test_every_row_names_its_sensor_and_scale(self):
        for r in D.variables_table(self._full()):
            assert r["sensor"] or r["value"] == "not available"

    def test_thermal_is_marked_as_a_100_m_measurement(self):
        row = next(r for r in D.variables_table(self._full())
                   if r["variable"] == "Surface temperature")
        assert row["scale"] == "100 m"

    def test_water_needed_is_labelled_needed_not_received(self):
        row = next(r for r in D.variables_table(self._full())
                   if "NEEDED" in r["variable"])
        assert "not received" in row["verdict"]

    def test_a_below_threshold_reading_says_below(self):
        row = next(r for r in D.variables_table(self._full())
                   if r["variable"] == "Vigour (NDVI)")
        assert row["verdict"] == "BELOW threshold"

    def test_an_unavailable_variable_says_so_and_carries_its_reason(self):
        row = next(r for r in D.variables_table(self._full())
                   if r["variable"] == "Greenness (EVI)")
        assert row["value"] == "not available"
        assert row["reason"] == "no pixels"

    def test_an_empty_record_produces_rows_that_all_say_not_available(self):
        rows = D.variables_table({})
        assert rows
        assert all(r["value"] in ("not available", "—") or "not available"
                   in str(r["value"]) for r in rows[:3])


# --- nutrition ladder and yield ----------------------------------------------

class TestNutritionAndYield:
    def test_a_relative_reading_offers_the_next_rung(self):
        n = D.nutrition_line({"nutrition": {
            "status": "OK", "claim_level": "relative",
            "relative_condition": "WITHIN SCHEME NORM"}})
        assert n["level"] == "relative"
        assert "reference strip" in n["next_step"]

    def test_a_sufficiency_reading_points_at_calibration(self):
        n = D.nutrition_line({"nutrition": {
            "status": "OK", "claim_level": "sufficiency",
            "sufficiency_index": 0.93, "sufficiency_reading": "marginal"}})
        assert "30 or more" in n["next_step"]

    def test_a_calibrated_reading_quotes_its_error_and_needs_no_next_step(self):
        n = D.nutrition_line({"nutrition": {
            "status": "OK", "claim_level": "calibrated", "nitrogen_pct": 2.8,
            "nitrogen_confidence": {"rmse_pct": 0.3, "n_points": 44}}})
        assert "RMSE 0.3%" in n["headline"]
        assert n["next_step"] is None

    def test_yield_without_calibration_shows_the_refusal_reason(self):
        line = D.yield_line({"yield_estimate": {
            "status": "OK", "claim_level": "relative", "yield_t_ha": None,
            "reason": "no calibrated yield model exists for sorghum"}})
        assert "no calibrated yield model" in line

    def test_yield_with_calibration_quotes_the_error(self):
        line = D.yield_line({"yield_estimate": {
            "yield_t_ha": 3.4,
            "confidence": {"rmse_fraction": 0.15, "n_points": 60}}})
        assert "3.4 t/ha" in line and "0.15" in line


# --- one classification, three places -----------------------------------------
#
# A live run showed Field 2 amber on the map, green in the ranked list, and
# "need attention: 0" in the header — three answers to one question on one
# screen, because the map used the four-state classification and the list used
# below_threshold alone.

class TestOneClassification:
    def _report(self):
        return {
            "fields": [_rec("A", 0.20, None), _rec("B", 0.45, None),
                       _rec("C", 0.60, None), _rec("D", 0.70, None)],
            "ranking": {"ranked": [
                {"rank": 1, "name": "A", "vigour": 0.20,
                 "below_threshold": False, "drivers": ["3.4 degC warmer"]},
                {"rank": 2, "name": "B", "vigour": 0.45,
                 "below_threshold": False, "drivers": []},
                {"rank": 3, "name": "C", "vigour": 0.60,
                 "below_threshold": False, "drivers": []},
                {"rank": 4, "name": "D", "vigour": 0.70,
                 "below_threshold": False, "drivers": []}],
                "unmeasured": []},
        }

    def test_the_list_marker_matches_the_map_status(self):
        report = self._report()
        att = D.attention_list(report)
        fc = {"features": [_square(33.0 + i * 0.01, 14.4, n)
                           for i, n in enumerate("ABCD")]}
        by_name = {f["name"]: f for f in D.map_features(report, fc)}
        for entry in att["ranked"]:
            assert entry["status"] == by_name[entry["name"]]["status"], \
                f"{entry['name']}: list and map disagree"

    def test_the_lowest_field_is_amber_in_both_not_green_in_one(self):
        att = D.attention_list(self._report())
        first = att["ranked"][0]
        assert first["name"] == "A"
        assert first["status"] == "watch"
        assert first["mark"] == "🟠"

    def test_the_header_counts_come_from_the_same_classification(self):
        att = D.attention_list(self._report())
        assert att["n_watch"] == 1
        assert att["n_attention"] == 0
        assert att["n_watch"] == sum(1 for e in att["ranked"]
                                     if e["status"] == "watch")

    def test_every_ranked_entry_explains_its_marker(self):
        for e in D.attention_list(self._report())["ranked"]:
            assert e["why"]

    def test_a_field_below_threshold_is_red_everywhere(self):
        report = {"fields": [_rec("A", 0.10, 0.30)],
                  "ranking": {"ranked": [{"rank": 1, "name": "A",
                                          "vigour": 0.10,
                                          "below_threshold": True,
                                          "drivers": []}],
                              "unmeasured": []}}
        att = D.attention_list(report)
        assert att["ranked"][0]["status"] == "attention"
        assert att["ranked"][0]["mark"] == "🔴"
        assert att["n_attention"] == 1


def test_the_demo_report_says_the_boundaries_are_invented():
    """
    The demo data carries REAL satellite measurements over ARBITRARY squares.
    That combination - real imagery, invented boundaries - is the most
    misleading one available, so the file must say so in both languages, and a
    future regeneration that drops the note must fail here.
    """
    import json
    path = os.path.join(os.path.dirname(__file__), "..", "docs",
                        "farm_report_demo.json")
    r = json.load(open(path, encoding="utf-8"))
    assert "DEMONSTRATION DATA" in r["note"]
    assert "belong to no farm" in r["note"]
    assert "invented" in r["note"]
    assert "مخترعة" in r["note_ar"]


class TestEtcMethodSurfacing:
    """The two ETc methods differ by about a fifth on a real field, and the
    difference is systematic rather than noise, so the app must say which one
    produced the number rather than showing a bare millimetre figure."""

    def test_the_integral_note_reports_scenes_and_coverage(self):
        note = D.etc_method_note({"water_requirement": {
            "etc_method": "sum over days of Kcb(NDVI on that day) * ET0",
            "canopy_series": {"observed_days": 75, "coverage": 0.832}}})
        assert "daily integral" in note
        assert "75" in note and "83%" in note

    def test_the_approximate_method_is_flagged_with_a_warning(self):
        note = D.etc_method_note({"water_requirement": {
            "etc_method": "APPROXIMATE: season-mean NDVI ..."}})
        assert note.startswith("⚠️")
        assert "APPROXIMATE" in note

    def test_the_table_marks_an_approximate_etc_in_its_verdict(self):
        rows = D.variables_table({"water_requirement": {
            "status": "OK", "et0_mm": 1792.0, "et0_mm_per_day": 6.6,
            "etc_mm": 385.0, "kcb": 0.215,
            "etc_method": "APPROXIMATE: season-mean NDVI ..."}})
        row = next(r for r in rows if "NEEDED" in r["variable"])
        assert "APPROXIMATE method" in row["verdict"]

    def test_no_water_requirement_yields_no_note(self):
        assert D.etc_method_note({}) is None


class TestPhenologyRows:
    def test_phenology_appears_when_computed(self):
        rows = D.variables_table({"phenology": {
            "status": "OK", "greenup_day": 75.0, "peak_day": 210.0,
            "peak_ndvi": 0.519, "season_length_days": 195.0}})
        labels = {r["variable"] for r in rows}
        assert "Green-up day" in labels
        assert "Season length" in labels

    def test_a_flat_field_says_why_there_is_no_phenology(self):
        rows = D.variables_table({"phenology": {
            "status": "NOT AVAILABLE",
            "reason": "amplitude too flat; it may simply not have been cropped"}})
        row = next(r for r in rows if "Green-up" in r["variable"])
        assert row["value"] == "not available"
        assert "not have been cropped" in row["reason"]
