"""
The crop library and the disease ladder.

The disease tests are mostly about what the module REFUSES to say. This is the
part of an agriculture product where the market lies: a field drawn red and
captioned with a pathogen name is a guess wearing the clothes of a measurement,
and the farmer pays for it with a fungicide they did not need.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import crops as C
import disease as DZ


# ==============================================================================
# THE CROP LIBRARY
# ==============================================================================

class TestCropsAreDistinct:
    def test_the_crops_a_gezira_tenancy_actually_grows_are_present(self):
        for key in ("sorghum", "wheat", "cotton", "groundnut", "sesame",
                    "maize", "onion", "faba_bean", "alfalfa", "sunflower"):
            assert key in C.CROPS, key

    def test_wheat_and_sorghum_do_not_share_a_heat_threshold(self):
        """The bug this library was written for. A wheat block inside a sorghum
        run was given sorghum's 38 degC threshold - six degrees above where
        wheat actually starts losing grain. The number was not missing; it was
        wrong, and nothing said so."""
        assert C.heat_stress_c("wheat") == 32.0
        assert C.heat_stress_c("sorghum") == 38.0
        assert C.gdd_base_c("wheat") != C.gdd_base_c("sorghum")

    def test_every_crop_carries_every_parameter(self):
        for key, c in C.CROPS.items():
            for field in ("ar", "en", "gdd_base_c", "heat_stress_c", "kc_ini",
                          "kc_mid", "kc_end", "root_depth_m", "season_days",
                          "problems"):
                assert field in c, f"{key} missing {field}"
            assert c["ar"] != c["en"], key
            assert c["kc_ini"] < c["kc_mid"], key

    def test_the_basis_says_these_are_not_sudanese_trials(self):
        assert "NOT Sudanese trial data" in C.BASIS


class TestResolvingACropName:
    def test_arabic_names_resolve(self):
        assert C.resolve("قمح") == "wheat"
        assert C.resolve("ذرة رفيعة") == "sorghum"
        assert C.resolve("قطن") == "cotton"

    def test_case_and_spacing_do_not_matter(self):
        assert C.resolve("  Sorghum ") == "sorghum"

    def test_an_unknown_crop_falls_back_rather_than_raising(self):
        """A report must still be produced for a field whose crop nobody
        recognised."""
        assert C.resolve("quinoa") == "default"

    def test_but_an_unrecognised_name_is_distinguishable_from_no_name(self):
        """"Nobody said what this is" and "somebody said something I did not
        understand" are different facts. The second is a mistake somewhere."""
        assert C.get("quinoa")["recognised"] is False
        assert C.get("quinoa")["declared"] == "quinoa"
        assert C.get(None)["declared"] is None
        assert C.get("sorghum")["recognised"] is True

    def test_nothing_resolves_to_a_crop_it_is_not(self):
        assert C.resolve("") == "default"
        assert C.resolve(None) == "default"

    def test_the_menu_offers_every_crop_with_unspecified_last(self):
        menu = C.names(ar=True)
        assert menu[-1][0] == "default"
        assert len(menu) == len(C.CROPS)
        assert ("wheat", "قمح") in menu


class TestTheCropLabelIsChecked:
    def test_a_canopy_far_outside_the_table_is_flagged(self):
        """The water calculation does not use the table - it derives Kcb from
        greenness on purpose. This asks a different question: if the canopy
        implies a coefficient nowhere near the published range, either the crop
        label is wrong or the field is not carrying what the label implies."""
        out = C.kcb_plausible(0.05, "cotton")
        assert out["plausible"] is False
        assert "crop label is wrong" in out["note"]
        assert out["note_ar"]

    def test_a_normal_canopy_passes_quietly(self):
        out = C.kcb_plausible(0.9, "wheat")
        assert out["plausible"] is True
        assert out["note"] == ""

    def test_no_kcb_is_not_a_failed_check(self):
        assert C.kcb_plausible(None, "wheat")["status"] == "NOT AVAILABLE"

    def test_wheat_sown_in_july_is_flagged(self):
        out = C.season_plausible("wheat", 7)
        assert out["plausible"] is False

    def test_wheat_sown_in_november_is_not(self):
        assert C.season_plausible("wheat", 11)["plausible"] is True

    def test_an_undeclared_sowing_month_is_not_a_verdict(self):
        assert C.season_plausible("wheat", None)["status"] == "NOT AVAILABLE"


# ==============================================================================
# HUMIDITY
# ==============================================================================

class TestHumidity:
    def test_dewpoint_equal_to_temperature_is_saturation(self):
        assert DZ.relative_humidity_pct(15.0, 15.0) == 100.0

    def test_a_dry_night_is_low(self):
        assert DZ.relative_humidity_pct(2.0, 30.0) < 25.0

    def test_a_dewpoint_above_air_temperature_is_clamped_not_supersaturated(self):
        """An artefact of gridded reanalysis, not physics."""
        assert DZ.relative_humidity_pct(20.0, 18.0) == 100.0

    def test_missing_data_gives_no_humidity_rather_than_zero(self):
        assert DZ.relative_humidity_pct(None, 20.0) is None
        assert DZ.relative_humidity_pct(10.0, None) is None

    def test_it_agrees_with_the_agronomy_module(self):
        """The formula is duplicated so this module imports without an Earth
        Engine dependency chain. Duplicated code that drifts is worse than a
        dependency, so the two are pinned together."""
        import agronomy
        for t in (5.0, 18.0, 32.0, 45.0):
            assert DZ._es_kpa(t) == pytest.approx(
                agronomy.saturation_vapour_pressure(t), rel=1e-9)


# ==============================================================================
# RUNG 2 - WEATHER FAVOURABILITY
# ==============================================================================

def _days(n, t_min, t_max, t_dew, rain):
    return ([t_min] * n, [t_max] * n, [t_dew] * n, [rain] * n)


class TestInfectionRisk:
    def test_a_warm_wet_fortnight_opens_the_anthracnose_window(self):
        tn, tx, td, r = _days(14, 24.0, 32.0, 23.0, 5.0)   # mean 28, wet
        out = DZ.infection_risk("sorghum_anthracnose", tn, tx, td, r)
        assert out["band"] == "FAVOURABLE"
        assert out["favourable_days"] >= out["days_needed"]

    def test_a_hot_dry_fortnight_does_not(self):
        tn, tx, td, r = _days(14, 25.0, 45.0, 2.0, 0.0)    # mean 35, bone dry
        out = DZ.infection_risk("sorghum_anthracnose", tn, tx, td, r)
        assert out["band"] == "NOT FAVOURABLE"
        assert out["favourable_days"] == 0

    def test_heavy_dew_counts_as_wetness_without_rain(self):
        """Sudanese winter wheat sits under heavy dew and almost no rain. A
        model that only counted rain would report no rust risk all season."""
        tn, tx, td, r = _days(14, 14.0, 26.0, 13.5, 0.0)   # mean 20, RH ~97%
        out = DZ.infection_risk("wheat_leaf_rust", tn, tx, td, r)
        assert out["band"] == "FAVOURABLE"

    def test_powdery_mildew_wants_humidity_without_free_water(self):
        """It is the exception among the leaf diseases, and the model says so
        by having no rain term at all."""
        assert DZ.PROBLEMS["powdery_mildew"]["weather_model"]["rain_mm"] is None

    def test_charcoal_rot_is_favoured_by_drought_not_wetness(self):
        hot_dry = _days(21, 32.0, 44.0, 5.0, 0.0)
        hot_wet = _days(21, 32.0, 44.0, 28.0, 12.0)
        assert DZ.infection_risk("charcoal_rot", *hot_dry)["band"] == "FAVOURABLE"
        assert DZ.infection_risk("charcoal_rot", *hot_wet)["band"] \
            == "NOT FAVOURABLE"

    def test_unknown_rain_is_not_treated_as_dryness(self):
        """For a drought-driven model, a missing rain figure must fail the test
        rather than pass it silently - otherwise every gap in the record
        becomes evidence of drought."""
        tn, tx, td = [32.0] * 21, [44.0] * 21, [5.0] * 21
        out = DZ.infection_risk("charcoal_rot", tn, tx, td, [None] * 21)
        assert out["favourable_days"] == 0

    def test_the_window_is_trailing_not_the_whole_season(self):
        """Two hundred days of season with a wet fortnight in the middle must
        not report the middle as current risk."""
        tn = [25.0] * 200
        tx = [45.0] * 200
        td = [2.0] * 200
        rain = [0.0] * 200
        for i in range(90, 104):                 # a wet spell long ago
            tx[i], td[i], rain[i] = 32.0, 24.0, 6.0
        now = DZ.infection_risk("sorghum_anthracnose", tn, tx, td, rain)
        then = DZ.infection_risk("sorghum_anthracnose", tn, tx, td, rain,
                                 as_of_index=103)
        assert now["band"] == "NOT FAVOURABLE"
        assert then["band"] == "FAVOURABLE"

    def test_every_risk_says_it_is_about_the_air_not_the_field(self):
        tn, tx, td, r = _days(14, 24.0, 32.0, 23.0, 5.0)
        out = DZ.infection_risk("sorghum_anthracnose", tn, tx, td, r)
        assert "NOT a detection" in out["claim"]
        assert "كل حقل تحت السماء نفسها" in out["claim_ar"]

    def test_every_risk_says_what_to_look_for_on_the_ground(self):
        """A risk band with no scouting description is an anxiety generator."""
        tn, tx, td, r = _days(14, 24.0, 32.0, 23.0, 5.0)
        out = DZ.infection_risk("sorghum_anthracnose", tn, tx, td, r)
        assert out["scout_for"] and out["scout_for_ar"]

    def test_no_weather_series_is_not_a_risk_of_zero(self):
        out = DZ.infection_risk("sorghum_anthracnose", [], [], [], [])
        assert out["status"] == "NOT AVAILABLE"
        assert "band" not in out


class TestProblemsWithNoWeatherModel:
    def test_a_vector_borne_virus_gets_no_invented_window(self):
        """A whitefly-transmitted virus is driven by insect population
        dynamics. A temperature window for it would produce a number every day
        and mean nothing."""
        assert DZ.PROBLEMS["cotton_leaf_curl"]["weather_model"] is None
        out = DZ.infection_risk("cotton_leaf_curl", [20], [30], [15], [0])
        assert out["status"] == "NO MODEL"
        assert "whitefly" in out["basis"]

    def test_a_soil_borne_wilt_gets_no_invented_window(self):
        assert DZ.PROBLEMS["fusarium_wilt"]["weather_model"] is None

    def test_a_migratory_pest_gets_no_invented_window(self):
        assert DZ.PROBLEMS["fall_armyworm"]["weather_model"] is None

    def test_they_are_returned_visibly_rather_than_omitted(self):
        """The absence of a risk line for fall armyworm must read as "nothing
        here can predict it", not as "it is fine"."""
        tn, tx, td, r = _days(14, 24.0, 32.0, 23.0, 5.0)
        out = DZ.crop_risk("maize", tn, tx, td, r)
        keys = {n["problem"] for n in out["no_model"]}
        assert "fall_armyworm" in keys

    def test_every_registered_problem_explains_its_basis_and_its_signs(self):
        for key, p in DZ.PROBLEMS.items():
            assert p["basis"], key
            assert p["scout_ar"] and p["scout_en"], key
            assert p["ar"] != p["en"], key
            assert p["kind"] in ("disease", "pest", "parasitic weed"), key

    def test_striga_is_labelled_a_parasitic_weed_not_a_disease(self):
        """It is the largest biological constraint on Sudanese sorghum and it
        is a flowering plant. Calling it a disease would send a farmer for a
        fungicide."""
        assert DZ.PROBLEMS["striga"]["kind"] == "parasitic weed"


class TestCropRisk:
    def test_each_crop_gets_only_its_own_problems(self):
        tn, tx, td, r = _days(14, 20.0, 28.0, 19.0, 2.0)
        wheat = {x["problem"] for x in DZ.crop_risk("wheat", tn, tx, td, r)["risks"]}
        assert wheat and not (wheat & {"cotton_bacterial_blight",
                                       "sesame_phyllody"})

    def test_the_worst_band_comes_first(self):
        tn, tx, td, r = _days(14, 14.0, 26.0, 13.5, 0.0)
        risks = DZ.crop_risk("wheat", tn, tx, td, r)["risks"]
        bands = [x["band"] for x in risks]
        order = {"FAVOURABLE": 0, "MARGINAL": 1, "NOT FAVOURABLE": 2}
        assert bands == sorted(bands, key=lambda b: order[b])

    def test_an_unknown_crop_has_no_registered_problems(self):
        """Silence, not a default crop's disease list. Showing sorghum's
        anthracnose risk for a field of unknown crop would be fabrication."""
        tn, tx, td, r = _days(14, 24.0, 32.0, 23.0, 5.0)
        out = DZ.crop_risk("quinoa", tn, tx, td, r)
        assert out["risks"] == [] and out["no_model"] == []


# ==============================================================================
# RUNG 1 - THE WITHIN-FIELD ANOMALY
# ==============================================================================

class TestAnomalyThreshold:
    def test_it_uses_the_field_s_own_spread(self):
        out = DZ.anomaly_threshold(0.30, 0.40, 0.50, k=2.0)
        assert out["robust_sigma"] == pytest.approx(0.10)
        assert out["threshold"] == pytest.approx(0.20)

    def test_a_uniform_field_has_no_outliers_and_says_so(self):
        """A field that is uniformly poor produces no anomaly, and that is
        correct. "Unlike the rest of this field" and "bad" are different
        statements, and only the first is being made."""
        out = DZ.anomaly_threshold(0.2, 0.2, 0.2)
        assert out["status"] == "NOT AVAILABLE"
        assert "no spread" in out["reason"]

    def test_a_missing_distribution_is_not_a_threshold_of_zero(self):
        assert DZ.anomaly_threshold(None, 0.4, 0.5)["status"] == "NOT AVAILABLE"

    def test_the_arbitrary_constant_is_labelled(self):
        assert "ARBITRARY" in DZ.anomaly_threshold(0.3, 0.4, 0.5)["basis"]


class TestBearing:
    def test_a_patch_to_the_north_reads_north(self):
        assert DZ.bearing([33.0, 14.0], [33.0, 14.01]) == "north"

    def test_a_patch_to_the_east_reads_east(self):
        assert DZ.bearing([33.0, 14.0], [33.01, 14.0]) == "east"

    def test_the_diagonals_work(self):
        assert DZ.bearing([33.0, 14.0], [33.01, 14.01]) == "north-east"
        assert DZ.bearing([33.0, 14.0], [33.01, 13.99]) == "south-east"
        assert DZ.bearing([33.0, 14.0], [32.99, 13.99]) == "south-west"

    def test_arabic_is_a_direction_not_a_transliteration(self):
        assert DZ.bearing([33.0, 14.0], [33.0, 14.01], ar=True) == "الشمال"

    def test_a_patch_at_the_centre_has_no_direction(self):
        assert DZ.bearing([33.0, 14.0], [33.0, 14.0]) is None


class TestAnomalyPatch:
    def test_a_real_patch_is_flagged_with_its_size_and_direction(self):
        out = DZ.anomaly_patch(4.0, 40.0, [33.0, 14.0], [33.0, 14.01])
        assert out["flagged"] is True
        assert out["area_ha"] == 4.0 and out["fraction"] == 0.1
        assert out["where"] == "north" and out["where_ar"] == "الشمال"

    def test_speckle_is_not_a_patch(self):
        out = DZ.anomaly_patch(0.2, 40.0, [33.0, 14.0], [33.0, 14.01])
        assert out["flagged"] is False
        assert "speckle" in out["reason"]

    def test_the_patch_names_no_cause(self):
        """The whole of rung 1. A satellite can say where to walk and how big.
        It cannot say why, and the sentence must carry that."""
        out = DZ.anomaly_patch(4.0, 40.0, [33.0, 14.0], [33.0, 14.01])
        assert "names no cause" in out["claim"]
        for word in ("disease", "salinity", "pest"):
            assert word in out["claim"]
        assert "اذهب وانظر" in out["claim_ar"]

    def test_no_area_is_not_an_absence_of_anomaly(self):
        assert DZ.anomaly_patch(None, 40.0)["status"] == "NOT AVAILABLE"


# ==============================================================================
# THE LADDER
# ==============================================================================

class TestTheLadder:
    def _favourable(self):
        tn, tx, td, r = _days(14, 24.0, 32.0, 23.0, 5.0)
        return DZ.crop_risk("sorghum", tn, tx, td, r)

    def _patch(self):
        return DZ.anomaly_patch(4.0, 40.0, [33.0, 14.0], [33.0, 14.01])

    def test_a_scouted_report_outranks_everything(self):
        out = DZ.diagnose(self._patch(), self._favourable(),
                          [{"problem": "sorghum_anthracnose",
                            "observed_at": "2022-09-01", "observer": "Ali"}])
        assert out["claim_level"] == "REPORTED"
        assert out["provenance"] == "REPORTED"
        assert out["problem"] == "sorghum_anthracnose"

    def test_the_satellite_never_produces_a_reported_claim(self):
        """The single most important assertion in this file. However bad the
        imagery looks, a satellite cannot name a disease."""
        for anomaly in (self._patch(), {}, None):
            out = DZ.diagnose(anomaly, self._favourable(), scouting=[])
            assert out["claim_level"] != "REPORTED"

    def test_an_anomaly_outranks_a_weather_window(self):
        """A patch is about THIS field. A risk band is about the sky over
        every field."""
        out = DZ.diagnose(self._patch(), self._favourable(), [])
        assert out["claim_level"] == "ANOMALY"
        assert out["problem"] is None

    def test_the_anomaly_says_where_to_walk_and_how_to_lift_it(self):
        out = DZ.diagnose(self._patch(), self._favourable(), [])
        assert "north" in out["headline"]
        assert "الشمال" in out["headline_ar"]
        assert out["next_step"] and out["next_step_ar"]

    def test_a_weather_window_alone_is_labelled_modelled(self):
        out = DZ.diagnose({}, self._favourable(), [])
        assert out["claim_level"] == "RISK"
        assert out["provenance"] == "MODELLED"
        assert "favourable" in out["headline"]

    def test_nothing_found_is_not_a_clean_bill_of_health(self):
        """A uniform problem across the whole field produces no anomaly, and a
        pest with no weather model produces no risk line. Silence here has two
        specific causes and the reader is told both."""
        out = DZ.diagnose({}, {"risks": []}, [])
        assert out["claim_level"] == "NONE"
        assert "not a clean bill of health" in out["note"]
        assert "ليست شهادة سلامة" in out["note_ar"]

    def test_the_latest_scouting_report_wins(self):
        out = DZ.diagnose(None, None, [
            {"problem": "striga", "observed_at": "2022-08-01"},
            {"problem": "sorghum_anthracnose", "observed_at": "2022-10-01"}])
        assert out["problem"] == "sorghum_anthracnose"

    def test_a_scouting_row_with_no_named_problem_is_not_a_diagnosis(self):
        """"I walked the field" is not "I found anthracnose"."""
        out = DZ.diagnose(None, None, [{"observed_at": "2022-10-01",
                                        "notes": "looked fine"}])
        assert out["claim_level"] == "NONE"

    def test_every_rung_is_bilingual(self):
        for out in (DZ.diagnose(None, None, [{"problem": "striga",
                                              "observed_at": "2022-08-01"}]),
                    DZ.diagnose(self._patch(), None, []),
                    DZ.diagnose(None, self._favourable(), []),
                    DZ.diagnose(None, None, [])):
            assert out["headline"] and out["headline_ar"]
            assert out["note"] and out["note_ar"]


class TestTheRefusalIsStatedInBothLanguages:
    def test_it_explains_why_and_not_merely_that(self):
        """A refusal that cannot say why reads as a missing feature."""
        assert "Sentinel-2" in DZ.REFUSAL
        assert "guess wearing the clothes of a measurement" in DZ.REFUSAL
        assert "تخمينٌ يلبس ثوب القياس" in DZ.REFUSAL_AR

    def test_it_names_the_cost_to_the_farmer(self):
        assert "spray" in DZ.REFUSAL
        assert "رشّة" in DZ.REFUSAL_AR
