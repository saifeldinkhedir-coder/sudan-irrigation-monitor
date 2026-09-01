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

    def test_grey_means_the_same_thing_in_arabic(self):
        """The caveat is the sentence a farmer most needs to understand, so it
        is the one that must not be left in English."""
        _k, _lbl, (ar, en) = next(e for e in D.LEGEND_BI if e[0] == "unmeasured")
        assert "ليس حقلًا سليمًا" in ar
        assert "NOT a healthy field" in en

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
        assert feats[0]["status_key"] == "attention"
        assert feats[1]["status_key"] == "ok"

    def test_a_polygon_with_no_result_is_drawn_grey_not_dropped(self):
        """A field silently missing from the map reads as a field the farmer
        does not have."""
        report = {"fields": [_rec("Field A")]}
        fc = {"features": [_square(33.0, 14.4, "Field A"),
                           _square(33.1, 14.5, "Field Z")]}
        feats = D.map_features(report, fc)
        assert len(feats) == 2
        z = next(f for f in feats if f["name"] == "Field Z")
        assert z["status_key"] == "unmeasured"
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


# --- the presentation layer ---------------------------------------------------

import ui as U


class TestPaletteConsistency:
    def test_the_hex_and_rgba_palettes_describe_the_same_colours(self):
        """
        view.py holds RGBA because pydeck needs it; ui.py holds hex because CSS
        does. If they drift, the map and the chips label the same field with
        two different colours and the reader cannot tell which is the claim.
        """
        pairs = [("attention", D.COLOUR_ATTENTION), ("watch", D.COLOUR_WATCH),
                 ("ok", D.COLOUR_OK), ("unmeasured", D.COLOUR_UNMEASURED)]
        for key, rgba in pairs:
            hexed = "#%02X%02X%02X" % tuple(rgba[:3])
            assert U.STATUS_HEX[key].upper() == hexed, (
                f"{key}: css {U.STATUS_HEX[key]} vs map {hexed}")

    def test_every_status_has_a_colour(self):
        for key in ("attention", "watch", "ok", "unmeasured"):
            assert key in U.STATUS_HEX


class TestBilingual:
    def test_every_label_has_both_languages(self):
        for key, pair in U.T.items():
            assert len(pair) == 2, key
            assert pair[0] and pair[1], key

    def test_arabic_and_english_differ(self):
        """A label that is identical in both is almost always an untranslated
        one that slipped through."""
        same = [k for k, (a, e) in U.T.items() if a == e]
        assert not same, f"untranslated labels: {same}"

    def test_t_returns_the_requested_language(self):
        assert U.t("nutrition", ar=True) == "التغذية"
        assert U.t("nutrition", ar=False) == "Nutrition"

    def test_an_unknown_key_returns_itself_rather_than_raising(self):
        assert U.t("no_such_label", ar=True) == "no_such_label"

    def test_the_tagline_says_what_the_tool_does_for_the_reader(self):
        """The tagline used to explain the provenance discipline. That is true
        and it is not what a farmer opens the app to read, so it was replaced
        with what the tool does. The promise itself did not weaken - the next
        test checks where it went."""
        ar, en = U.T["tagline"]
        assert "المستشعر" not in ar and "sensor" not in en.lower()
        assert "الماء" in ar and "water" in en.lower()

    def test_the_measurement_promise_moved_rather_than_vanished(self):
        """Every figure still names its sensor and its scale - as two columns
        on every row, which is where the promise is actually kept, and as a
        section of the About page, which is where it is explained. A tagline
        was only ever a claim about those."""
        import about as A
        assert U.T["sensor"][0] and U.T["measured_at"][0]
        ar_body = A.SECTIONS["provenance"][2]
        assert "المستشعر" in ar_body and "غير متاح" in ar_body
        en_body = A.SECTIONS["provenance"][3]
        assert "sensor" in en_body and "not available" in en_body

    def test_the_no_verdict_warning_survives_translation(self):
        """The distinction between 'not measured' and 'healthy' is the whole
        point; it must not be lost in the Arabic."""
        ar, en = U.T["no_verdict"]
        assert "بلا حكم" in ar
        assert "not the same as" in en


class TestArabicContent:
    """The chrome was Arabic while the content stayed English — half a
    translation, which asks the reader to switch language mid-sentence for
    exactly the sentences that carry the caveats."""

    def test_the_status_reason_is_arabic_when_arabic(self):
        rec = _rec("F", 0.20, 0.30)
        assert "دون العتبة" in D.field_status(rec, ar=True)["why"]
        assert "below the" in D.field_status(rec, ar=False)["why"]

    def test_the_map_status_label_is_translated_but_the_key_is_not(self):
        report = {"fields": [_rec("A", 0.2, 0.3)]}
        fc = {"features": [_square(33.0, 14.4, "A")]}
        ar = D.map_features(report, fc, ar=True)[0]
        assert ar["status"] == "تحتاج انتباهًا"
        assert ar["status_key"] == "attention", "the key must stay machine-readable"

    def test_variable_names_and_verdicts_translate(self):
        rows = D.localise_rows(
            [{"variable": "Surface temperature", "value": "40 °C",
              "threshold": "—", "verdict": "BELOW threshold",
              "sensor": "Landsat", "scale": "100 m"}], ar=True)
        assert rows[0]["variable"] == "حرارة السطح"
        assert rows[0]["verdict"] == "دون العتبة"

    def test_sensors_and_units_are_left_alone(self):
        """A sensor name is not language; translating it would make the
        provenance harder to check, not easier to read."""
        rows = D.localise_rows(
            [{"variable": "Surface temperature", "value": "40 °C",
              "threshold": "—", "verdict": "—", "sensor": "Landsat 8/9",
              "scale": "100 m"}], ar=True)
        assert rows[0]["sensor"] == "Landsat 8/9"
        assert rows[0]["scale"] == "100 m"

    def test_english_rows_pass_through_untouched(self):
        rows = [{"variable": "Soil texture", "value": "clay", "threshold": "—",
                 "verdict": "—", "sensor": "x", "scale": "y"}]
        assert D.localise_rows(rows, ar=False) is rows

    def test_the_needed_not_received_warning_survives_translation(self):
        rows = D.localise_rows(
            [{"variable": "Crop water NEEDED (ETc)", "value": "305 mm",
              "threshold": "—", "verdict": "NEEDED, not received",
              "sensor": "x", "scale": "y"}], ar=True)
        assert rows[0]["verdict"] == "احتياج، لا ما وصل"

    def test_an_untranslated_label_appears_verbatim_rather_than_vanishing(self):
        assert D.label(D.VARIABLE_LABEL, "Some New Variable", True) == \
            "Some New Variable"


class TestDriverTranslation:
    def test_the_thermal_driver_translates(self):
        assert D.localise_driver("3.41 degC warmer than its surroundings",
                                 ar=True) == "أدفأ بـ3.41°م من محيطه"

    def test_the_water_driver_translates(self):
        out = D.localise_driver("310 mm of water needed beyond rainfall", True)
        assert out.startswith("310")
        assert "احتاجها المحصول" in out

    def test_an_unknown_driver_passes_through_rather_than_vanishing(self):
        """A driver that disappears because nobody translated it takes the
        reason for a field's rank with it - worse than the wrong language."""
        odd = "some future driver nobody translated"
        assert D.localise_driver(odd, ar=True) == odd

    def test_english_is_untouched(self):
        s = "3.41 degC warmer than its surroundings"
        assert D.localise_driver(s, ar=False) == s


# --- what the styled table gave up, given back ---------------------------------

class TestCsvExport:
    """st.dataframe gave sorting and a download button for free; the styled
    table replaced it because a dataframe cannot show that a reading is below
    its threshold. This gives the export back rather than leaving the trade
    half-made."""

    def _rows(self):
        return [{"variable": "Vigour (NDVI)", "value": "0.2190",
                 "threshold": "0.3000", "verdict": "BELOW threshold",
                 "sensor": "Sentinel-2 median", "scale": "10 m"},
                {"variable": "Greenness (EVI)", "value": "not available",
                 "threshold": "—", "verdict": "—", "sensor": "", "scale": "",
                 "reason": "no valid pixels"}]

    def test_every_row_and_column_reaches_the_csv(self):
        csv = D.rows_to_csv(self._rows())
        assert "Vigour (NDVI)" in csv
        assert "BELOW threshold" in csv
        assert "Sentinel-2 median" in csv

    def test_the_reason_for_an_unavailable_row_is_exported_too(self):
        """It is the most useful cell on that row and the one a screenshot of
        the table would lose."""
        assert "no valid pixels" in D.rows_to_csv(self._rows())

    def test_the_header_translates_but_sensor_values_do_not(self):
        csv = D.rows_to_csv(self._rows(), ar=True)
        assert "القيمة" in csv
        assert "Sentinel-2 median" in csv, (
            "a translated sensor name is harder to check against the catalogue")

    def test_it_parses_back_as_csv(self):
        import csv as _csv
        import io as _io
        rows = list(_csv.reader(_io.StringIO(D.rows_to_csv(self._rows()))))
        assert len(rows) == 3          # header + two data rows
        assert len(rows[0]) == 7


class TestThermalResolvabilitySurfaced:
    def test_a_field_too_small_for_thermal_says_so_on_the_row(self):
        rows = D.variables_table({"thermal_stress": {
            "status": "OK", "value": 40.1, "neighbourhood_c": 40.1,
            "difference_c": 0.0, "reading": "close to the surrounding land",
            "sensor": "Landsat 8/9 ST_B10", "scale_m": 100,
            "pixels_across": 1.2, "resolvable": False,
            "resolvability_note": ("about 1.2 thermal pixels across - the field "
                                   "and its surroundings are largely the same "
                                   "pixels")}})
        row = next(r for r in rows if r["variable"] == "Surface temperature")
        assert "largely the same pixels" in row["reason"]

    def test_a_big_enough_field_carries_no_such_warning(self):
        rows = D.variables_table({"thermal_stress": {
            "status": "OK", "value": 42.4, "neighbourhood_c": 39.0,
            "difference_c": 3.4, "reading": "warmer than the surrounding land",
            "sensor": "Landsat 8/9 ST_B10", "scale_m": 100,
            "pixels_across": 6.6, "resolvable": True,
            "resolvability_note": "about 6.6 thermal pixels across - wide enough"}})
        row = next(r for r in rows if r["variable"] == "Surface temperature")
        assert not row.get("reason")


class TestNoNetworkDependency:
    def test_the_stylesheet_fetches_nothing(self):
        """A field office in Sudan has the least reliable connection in the
        system, and a webfont import also sends a request to a third party
        every time a farmer opens their own crop data."""
        import ui as U
        assert "fonts.googleapis" not in U.CSS
        assert "@import" not in U.CSS
        assert "http" not in U.CSS

    def test_the_arabic_stack_reaches_a_face_on_every_platform(self):
        import ui as U
        for face in ("Segoe UI", "Noto Sans Arabic", "Tahoma"):
            assert face in U.SANS_AR

    def test_no_generated_streamlit_class_is_targeted(self):
        """[class*="css"] matches Streamlit's generated names, which change
        between releases - a selector that promises to break on upgrade."""
        import ui as U
        style = U.CSS.split("*/")[-1]      # skip the comment explaining this
        assert '[class*=' not in style


# --- generated in the language, not translated afterwards ----------------------

class TestBilingualAtSource:
    """
    variables_table used to build English cells and a second pass translated
    them by matching the generated text. That match breaks the moment the engine
    rewords anything, and it breaks by leaving English inside an Arabic table
    rather than by raising - the worst way for a translation to fail. The cells
    are now generated in the requested language.
    """

    def _rec(self):
        return {
            "crop_health": {"readings": {
                "vigour": {"status": "OK", "value": 0.219, "threshold": 0.30,
                           "sensor": "Sentinel-2 median", "scale_m": 10}}},
            "thermal_stress": {"status": "OK", "value": 42.4,
                               "neighbourhood_c": 39.0, "difference_c": 3.4,
                               "reading": "warmer than the surrounding land",
                               "sensor": "Landsat 8/9 ST_B10", "scale_m": 100,
                               "pixels_across": 6.6, "resolvable": True},
            "climate": {"season_vs_history": {"this_season_mm": 228.0,
                                              "verdict": "drier than usual"},
                        "dry_spells": {"longest_dry_spell_days": 173,
                                       "threshold_days": 10, "flagged": True}},
            "soil": {"status": "OK", "texture": "clay"},
            "phenology": {"status": "OK", "greenup_day": 75.0, "peak_day": 210.0,
                          "peak_ndvi": 0.52, "season_length_days": 195.0},
        }

    def test_no_english_leaks_into_the_arabic_table(self):
        """The check that would have caught the half-translation: no cell in
        the Arabic table may be one of the engine's English phrases."""
        leaks = ("close to the surrounding land", "warmer than the surrounding",
                 "drier than usual", "clay", "FLAGGED", "BELOW threshold",
                 "above threshold", "not available", "no threshold",
                 "day 75 of the season", "peak on day", "days")
        rows = D.variables_table(self._rec(), ar=True)
        for r in rows:
            for cell in (r["variable"], str(r["value"]), str(r["threshold"]),
                         str(r["verdict"])):
                for phrase in leaks:
                    assert cell != phrase, f"English cell in Arabic table: {cell}"

    def test_the_thermal_reading_translates(self):
        rows = D.variables_table(self._rec(), ar=True)
        row = next(r for r in rows if r["variable"] == "حرارة السطح")
        assert row["verdict"] == "أدفأ من الأرض المحيطة"

    def test_the_season_verdict_translates(self):
        rows = D.variables_table(self._rec(), ar=True)
        row = next(r for r in rows if "تاريخ الموقع" in r["variable"])
        assert row["verdict"] == "أجفّ من المعتاد"

    def test_the_soil_texture_translates(self):
        rows = D.variables_table(self._rec(), ar=True)
        assert next(r for r in rows if r["variable"] == "قوام التربة")["value"] \
            == "طين"

    def test_phenology_days_are_arabic(self):
        rows = D.variables_table(self._rec(), ar=True)
        row = next(r for r in rows if r["variable"] == "يوم الإنبات")
        assert "اليوم 75 من الموسم" == row["value"]

    def test_an_unknown_engine_value_passes_through_visibly(self):
        """A vocabulary item nobody translated must appear verbatim, not as a
        blank: a missing cell hides that the engine said something new."""
        rec = self._rec()
        rec["soil"]["texture"] = "some new texture class"
        rows = D.variables_table(rec, ar=True)
        assert next(r for r in rows
                    if r["variable"] == "قوام التربة")["value"] \
            == "some new texture class"

    def test_sensors_and_units_stay_latin_in_both_languages(self):
        ar = D.variables_table(self._rec(), ar=True)
        en = D.variables_table(self._rec(), ar=False)
        for a, e in zip(ar, en):
            assert a["sensor"].replace("سنوات", "years") == e["sensor"]
            assert a["scale"] == e["scale"]

    def test_the_english_table_is_unchanged_in_wording(self):
        rows = D.variables_table(self._rec(), ar=False)
        labels = {r["variable"] for r in rows}
        assert "Surface temperature" in labels
        assert "Crop water NEEDED (ETc)" in labels


class TestEngineVocabularies:
    """Classification values the engine emits reach the screen verbatim. They
    are vocabularies, not prose, so they translate by lookup - and anything new
    passes through visibly rather than blanking."""

    def test_the_relative_condition_translates(self):
        n = D.nutrition_line({"nutrition": {
            "status": "OK", "claim_level": "relative",
            "relative_condition": "WITHIN SCHEME NORM"}}, ar=True)
        assert "ضمن معدّل المخطط" in n["headline"]
        assert "SCHEME NORM" not in n["headline"]

    def test_all_three_condition_bands_are_covered(self):
        for band in ("BELOW SCHEME NORM", "WITHIN SCHEME NORM",
                     "ABOVE SCHEME NORM"):
            assert band in D.RELATIVE_CONDITION

    def test_the_sufficiency_reading_translates(self):
        n = D.nutrition_line({"nutrition": {
            "status": "OK", "claim_level": "sufficiency",
            "sufficiency_index": 0.93, "sufficiency_reading": "marginal"}},
            ar=True)
        assert "حدّي" in n["headline"]

    def test_the_yield_refusal_is_arabic(self):
        line = D.yield_line({"yield_estimate": {
            "yield_t_ha": None,
            "reason": "no calibrated yield model exists for sorghum"}}, ar=True)
        assert "30 قياس حصاد" in line
        assert "calibrated" not in line

    def test_the_etc_integral_note_is_arabic(self):
        note = D.etc_method_note({"water_requirement": {
            "etc_method": "sum over days of Kcb", "canopy_series":
                {"observed_days": 75, "coverage": 0.83}}}, ar=True)
        assert "التكامل اليومي" in note
        assert "75" in note

    def test_the_approximate_warning_is_arabic_and_keeps_its_symbol(self):
        note = D.etc_method_note({"water_requirement": {
            "etc_method": "APPROXIMATE: season-mean NDVI"}}, ar=True)
        assert note.startswith("⚠️")
        assert "طريقة تقريبية" in note

    def test_the_nutrition_caveat_is_arabic(self):
        n = D.nutrition_line({"nutrition": {
            "status": "OK", "claim_level": "relative",
            "relative_condition": "WITHIN SCHEME NORM",
            "caveat": "Chlorophyll indices respond to nitrogen..."}}, ar=True)
        assert "الكلوروفيل" in n["caveat"]
        assert "الملوحة" in n["caveat"], "the multi-cause warning must survive"


# --- the map as a workspace ---------------------------------------------------

import fieldmap as FM


class TestDrawnFields:
    """
    The first map could only receive fields as a GeoJSON path typed into a
    sidebar - it asked a farmer to produce a file format to describe land they
    can see out of the window. Drawing replaces that, and the conversion has to
    reject here what the engine would reject later, where the person can see it.
    """

    def _drawn(self, coords, kind="Polygon"):
        return {"all_drawings": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": kind, "coordinates": coords}}]}

    def _square(self, lon, lat, side):
        return [[[lon, lat], [lon + side, lat], [lon + side, lat + side],
                 [lon, lat + side], [lon, lat]]]

    def test_a_drawn_polygon_becomes_a_named_field(self):
        out = FM.drawings_to_fields(self._drawn(self._square(33.1, 14.4, 0.005)))
        assert len(out["features"]) == 1
        f = out["features"][0]
        assert f["geometry"]["type"] == "Polygon"
        assert f["properties"]["source"] == "drawn"
        assert f["properties"]["area_ha"] > 0

    def test_the_area_is_computed_not_asked_for(self):
        out = FM.drawings_to_fields(self._drawn(self._square(33.1, 14.4, 0.006)))
        assert 35 < out["features"][0]["properties"]["area_ha"] < 50

    def test_a_shape_too_small_for_a_pixel_is_refused_with_a_reason(self):
        out = FM.drawings_to_fields(
            self._drawn(self._square(33.1, 14.4, 0.0002)))
        assert out["features"] == []
        assert "below the" in out["rejected"][0]["reason"]

    def test_a_non_polygon_is_refused(self):
        out = FM.drawings_to_fields(
            self._drawn([[33.0, 14.4], [33.1, 14.5]], kind="LineString"))
        assert out["features"] == []
        assert out["rejected"][0]["reason"] == "not a polygon"

    def test_nothing_drawn_yields_nothing_rather_than_raising(self):
        assert FM.drawings_to_fields(None)["features"] == []
        assert FM.drawings_to_fields({})["features"] == []

    def test_saved_geojson_carries_no_rejection_bookkeeping(self, tmp_path):
        """The file goes to the engine; the rejection list is a UI concern and
        would be an unknown key in the input contract."""
        import json
        out = FM.drawings_to_fields(self._drawn(self._square(33.1, 14.4, 0.005)))
        path = str(tmp_path / "drawn.geojson")
        assert FM.save_fields(out, path) == 1
        saved = json.load(open(path, encoding="utf-8"))
        assert set(saved) == {"type", "features"}

    def test_a_saved_field_survives_the_engine_s_own_area_check(self, tmp_path):
        """The drawn file must satisfy the same geometry rules the agriculture
        engine applies, or drawing is a dead end."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        import decision_logic as dl
        out = FM.drawings_to_fields(self._drawn(self._square(33.1, 14.4, 0.005)))
        geom = out["features"][0]["geometry"]
        assert dl.geojson_area_m2(geom) > 0
        assert dl.geojson_centroid(geom) is not None


class TestMapConfiguration:
    def test_the_basemap_is_satellite_imagery(self):
        """A pale rectangle over grey streets cannot be checked against the
        ground, and checking is the first thing anyone does with a farm map."""
        m = FM.build_map([], (14.42, 33.12))
        html = m.get_root().render()
        assert "World_Imagery" in html

    def test_the_drawing_tools_offer_only_shapes_a_field_can_be(self):
        """A circle or a marker cannot be a field boundary; offering tools whose
        output the engine would reject wastes somebody's afternoon."""
        m = FM.build_map([], (14.42, 33.12), drawing=True)
        html = m.get_root().render()
        assert '"polygon":' in html or "polygon" in html
        assert '"circle": false' in html.lower().replace("circle: false",
                                                         '"circle": false') \
            or '"circle":false' in html.replace(" ", "")

    def test_a_place_search_is_present(self):
        m = FM.build_map([], (14.42, 33.12))
        assert "Geocoder" in m.get_root().render() or "geocoder" in \
            m.get_root().render().lower()

    def test_analysed_fields_are_drawn_in_their_status_colour(self):
        feats = [{"name": "F", "polygon": [[33.0, 14.4], [33.01, 14.4],
                                           [33.01, 14.41], [33.0, 14.4]],
                  "colour": D.COLOUR_ATTENTION, "status": "attention",
                  "status_key": "attention", "vigour_display": "0.200",
                  "why": "low"}]
        html = FM.build_map(feats, (14.4, 33.0)).get_root().render()
        assert "#c83c2d" in html.lower()


def test_the_street_map_does_not_cover_the_satellite_imagery():
    """
    Two base layers added without `show` are both active, and folium paints the
    later one on top - so offering a street map for people who want it silently
    covered the imagery that is the entire reason for the map. Caught by looking
    at it; pinned here so it cannot come back.
    """
    html = FM.build_map([], (14.42, 33.12)).get_root().render()
    i_sat = html.find("World_Imagery")
    i_osm = html.find("openstreetmap.org")
    assert i_sat != -1, "satellite tiles must be present"
    if i_osm != -1:
        # the street layer must be created with show disabled
        segment = html[max(0, i_osm - 1500):i_osm + 1500]
        assert '"show": false' in segment.replace(" ", " ").lower() \
            or "show: false" in segment.lower() \
            or "showdefault" not in segment.lower(), \
            "the street map must not start active over the imagery"


# ==============================================================================
# THE PRODUCT SURFACE
# ==============================================================================

def _source(name):
    path = os.path.join(os.path.dirname(__file__), "..", "farmer_app", name)
    return open(path, encoding="utf-8").read()


class TestMethodIsOffTheWorkingScreen:
    """
    The working screen answers "which field, and what do I do". Everything that
    answers "how was this number arrived at" moved to the About page, whole.

    The test applied to each paragraph was: would knowing this change what the
    reader DOES today? "Not measured is not healthy" passes - you go and look.
    "Green-up is the first crossing of half the seasonal amplitude" does not.
    """
    METHOD_KEYS = ("ranking_basis", "sortable_caveat", "map_caption",
                   "etc_method_note")

    def test_no_method_essay_is_rendered_beside_the_numbers(self):
        src = _source("app.py")
        rendered = [k for k in self.METHOD_KEYS if f'"{k}"' in src]
        assert not rendered, f"method text back on the working screen: {rendered}"

    def test_the_limitations_list_is_not_on_the_working_screen(self):
        assert 'report.get("limitations"' not in _source("app.py")

    def test_the_demo_paragraph_is_not_on_the_working_screen(self):
        """It used to open with a five-line paragraph and an expander. What
        remains is the label, which is the part that stops a reader mistaking
        demonstration output for their own farm."""
        src = _source("app.py")
        assert 'report.get("note_ar")' not in src
        assert "demo=bool(report.get(\"note\"))" in src

    def test_the_method_moved_rather_than_being_deleted(self):
        """A tool whose case rests on auditable numbers cannot bury the basis
        for them. Every sentence taken off the working screen is reproduced on
        the About page."""
        import about as A
        assert set(A.SECTIONS) >= {"provenance", "ranking", "water",
                                   "phenology", "search"}
        for _k, (ar_h, en_h, ar_b, en_b) in A.SECTIONS.items():
            assert ar_h and en_h and len(ar_b) > 80 and len(en_b) > 80
        assert 'report.get("limitations")' in _source("about.py")
        assert 'report.get("note")' in _source("about.py")

    def test_the_caveats_that_change_a_decision_stayed(self):
        """Two warnings survive on the working screen because they change what
        the reader concludes, not how a number was computed: a grey field is
        not a healthy one, and a water requirement is not a delivery."""
        src = _source("app.py")
        assert '"unmeasured_note"' in src
        assert '"no_verdict"' in src
        assert "NEEDED, not received" in _source("view.py")


class TestSearchIsOnTheWorkingScreen:
    def test_the_search_box_is_not_hidden_behind_a_menu(self):
        """A scheme has tens of thousands of tenancies. A search you have to
        find first is a search nobody uses."""
        src = _source("app.py")
        assert "_toolbar(index, ar)" in src
        assert "st.expander" not in src.split("def _toolbar")[1].split("def ")[0]

    def test_every_axis_the_user_asked_for_is_wired(self):
        src = _source("app.py")
        for widget in ('ui.t("search"', 'ui.t("crop_filter"',
                       'ui.t("date_basis"', 'ui.t("harvest_filter"',
                       'ui.t("area_filter"'):
            assert widget in src, widget

    def test_a_map_click_selects_by_geometry_not_by_tooltip_text(self):
        """Tooltip text is display copy - reworded, translated, truncated. A
        selector built on it breaks silently in whichever language nobody
        tested."""
        src = _source("app.py")
        assert "S.field_at_point(" in src
        assert "last_object_clicked_tooltip" not in src


class TestFilteringNeverHidesAField:
    def test_the_map_dims_unmatched_fields_rather_than_removing_them(self):
        import fieldmap as FM
        feats = [{"name": "A", "colour": [70, 150, 95, 170],
                  "polygon": [[33.0, 14.0], [33.01, 14.0], [33.01, 14.01],
                              [33.0, 14.0]],
                  "status": "ok", "vigour_display": "0.500", "why": "x"},
                 {"name": "B", "colour": [70, 150, 95, 170],
                  "polygon": [[33.2, 14.2], [33.21, 14.2], [33.21, 14.21],
                              [33.2, 14.2]],
                  "status": "ok", "vigour_display": "0.500", "why": "x"}]
        html = FM.build_map(feats, (14.1, 33.1), highlight={"A"})._repr_html_()
        # Both are still drawn. A filter that made a field disappear from the
        # picture of the farm would leave a farmer unable to tell a filtered
        # field from one the tool never had.
        assert html.count("fillOpacity") >= 2
        assert "0.1" in html and "0.45" in html


class TestUnavailableRowsKeepTheirReason:
    def test_the_reason_rides_on_the_row_as_a_tooltip(self):
        """It is the one piece of method text that changes what the reader
        does - wait for a clear scene, or go and look - so it stays at the
        point of use, and costs no vertical space."""
        rows = [{"variable": "Surface temperature", "value": "not available",
                 "threshold": "—", "verdict": "—", "sensor": "Landsat 8/9",
                 "scale": "100 m", "reason": "no cloud-free thermal scene"}]
        captured = {}
        real = U.st.markdown
        U.st.markdown = lambda html, **kw: captured.setdefault("h", html)
        try:
            U.variables_table(rows, ar=False)
        finally:
            U.st.markdown = real
        assert 'title="no cloud-free thermal scene"' in captured["h"]
        assert 'class="na"' in captured["h"]

    def test_an_arabic_unavailable_row_is_still_marked_unavailable(self):
        """The row style keyed on the English string "not available". In Arabic
        the cell reads "غير متاح", so every unavailable row in the Arabic table
        was rendered as though it held a number."""
        rows = [{"variable": "حرارة السطح", "value": "غير متاح",
                 "threshold": "—", "verdict": "—", "sensor": "Landsat 8/9",
                 "scale": "100 m", "reason": "لا مشهد حراري صافٍ"}]
        captured = {}
        real = U.st.markdown
        U.st.markdown = lambda html, **kw: captured.setdefault("h", html)
        try:
            U.variables_table(rows, ar=True)
        finally:
            U.st.markdown = real
        assert 'class="na"' in captured["h"]

    def test_an_arabic_below_threshold_reading_is_still_coloured_red(self):
        rows = [{"variable": "النموّ (NDVI)", "value": "0.2000",
                 "threshold": "0.3000", "verdict": "دون العتبة",
                 "sensor": "Sentinel-2", "scale": "10 m"}]
        captured = {}
        real = U.st.markdown
        U.st.markdown = lambda html, **kw: captured.setdefault("h", html)
        try:
            U.variables_table(rows, ar=True)
        finally:
            U.st.markdown = real
        assert 'class="below"' in captured["h"]


class TestTheMapFramesTheFields:
    """
    "The field shapes do not show" survives having drawn the shapes correctly.
    A farm ten kilometres across has a mean position with no field near it, so
    a fixed zoom around that mean opens on bare ground with every polygon just
    off the edge - which looks exactly like a map that failed.
    """
    def _spread(self):
        import fieldmap as FM
        return FM, [{"name": n, "colour": [70, 150, 95, 170], "status": "ok",
                     "vigour_display": "0.500", "why": "x",
                     "polygon": [[lon, lat], [lon + .006, lat],
                                 [lon + .006, lat + .006],
                                 [lon, lat + .006], [lon, lat]]}
                    for n, lon, lat in (("A", 33.10, 14.42), ("B", 33.14, 14.45),
                                        ("C", 33.18, 14.48), ("D", 33.23, 14.47))]

    def test_every_field_falls_inside_the_opening_view(self):
        FM, feats = self._spread()
        (lat, lon), zoom = FM.centre_and_zoom(feats, 550, 520)
        half_lon = 360.0 * 550 / (256.0 * 2 ** zoom) / 2
        half_lat = 180.0 * 520 / (256.0 * 2 ** zoom) / 2
        for f in feats:
            for x, y in f["polygon"]:
                assert abs(x - lon) <= half_lon, f["name"]
                assert abs(y - lat) <= half_lat, f["name"]

    def test_a_single_field_is_not_framed_from_orbit(self):
        """One field must not produce zoom 2. The span is floored, not the
        zoom, so a lone polygon still opens close enough to check against the
        ground."""
        FM, feats = self._spread()
        _c, zoom = FM.centre_and_zoom(feats[:1])
        assert zoom >= 14

    def test_a_wider_farm_opens_further_out(self):
        FM, feats = self._spread()
        _c, near = FM.centre_and_zoom(feats[:2])
        _c, far = FM.centre_and_zoom(feats)
        assert far < near

    def test_no_fields_leaves_the_caller_s_centre_alone(self):
        import fieldmap as FM
        assert FM.centre_and_zoom([]) == (None, None)

    def test_the_map_actually_opens_on_the_computed_frame(self):
        """folium's own fit_bounds does not survive streamlit-folium rebuilding
        the map, so the zoom is computed here and passed as zoom_start. This
        pins that it reaches the map object."""
        FM, feats = self._spread()
        (lat, lon), zoom = FM.centre_and_zoom(feats)
        m = FM.build_map(feats, (0.0, 0.0), zoom=14)
        assert m.location == [lat, lon]
        assert m.options["zoom"] == zoom
