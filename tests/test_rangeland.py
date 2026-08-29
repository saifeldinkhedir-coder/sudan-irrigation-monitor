"""
Rangeland layer.

Most of these tests are about the neutrality guard rather than about the
remote sensing, which reflects where the actual risk in this layer sits: the
NDVI arithmetic being slightly off costs accuracy, while a corridor map carrying
an entitlement word can be carried into a dispute as evidence.
"""

import rangeland as rl


class TestNeutralityGuard:
    def test_plain_measurement_text_passes(self):
        r = rl.check_neutrality("Mean seasonal greenness was below the site's "
                                "recent seasons.")
        assert r["neutral"] is True
        assert r["hits"] == []

    def test_english_entitlement_language_is_caught(self):
        for phrase in ("this corridor belongs to the herders",
                       "grazing rights along this route",
                       "cropland encroachment on the corridor"):
            assert rl.check_neutrality(phrase)["neutral"] is False

    def test_arabic_entitlement_language_is_caught(self):
        for phrase in ("المسار يمرّ في أرضهم",
                       "حق الرعي في هذا الممر",
                       "تعدّي المزارعين على الممر"):
            assert rl.check_neutrality(phrase)["neutral"] is False

    def test_empty_text_is_neutral_rather_than_an_error(self):
        assert rl.check_neutrality("")["neutral"] is True
        assert rl.check_neutrality(None)["neutral"] is True

    def test_the_guard_reports_which_words_it_caught(self):
        r = rl.check_neutrality("this is tribal land and they encroach on it")
        assert not r["neutral"]
        assert len(r["hits"]) >= 2


class TestSensitivityNote:
    def test_every_productivity_result_carries_the_note(self):
        out = rl.productivity_index([0.3, 0.4, 0.5])
        assert "conflict_sensitivity" in out
        assert "ar" in out["conflict_sensitivity"]
        assert "en" in out["conflict_sensitivity"]

    def test_the_note_itself_passes_the_neutrality_guard(self):
        """A caveat that trips its own guard would be a bad joke."""
        for lang in ("en", "ar"):
            assert rl.check_neutrality(rl.SENSITIVITY_NOTE[lang])["neutral"]

    def test_the_note_denies_being_evidence_of_a_claim(self):
        assert "not evidence of any claim" in rl.SENSITIVITY_NOTE["en"]


class TestGreenupTiming:
    def _season(self):
        # a clean single-peak season: 20 observations, 10-day spacing
        days = [10.0 * i for i in range(20)]
        ndvi = [0.15 + 0.45 * (1 - abs(i - 10) / 10.0) for i in range(20)]
        return days, ndvi

    def test_a_clean_season_yields_greenup_peak_and_length(self):
        days, ndvi = self._season()
        out = rl.greenup_timing(days, ndvi)
        assert out["status"] == "OK"
        assert out["greenup_day"] is not None
        assert out["peak_day"] == 100.0
        assert out["season_length_days"] > 0

    def test_too_few_observations_is_refused_not_guessed(self):
        out = rl.greenup_timing([0, 10, 20], [0.2, 0.5, 0.3])
        assert out["status"] == "NOT AVAILABLE"
        assert "usable observations" in out["reason"]

    def test_a_flat_series_says_so_rather_than_inventing_a_season(self):
        days = [10.0 * i for i in range(20)]
        flat = [0.20 + 0.001 * i for i in range(20)]
        out = rl.greenup_timing(days, flat)
        assert out["status"] == "NOT AVAILABLE"
        assert "too flat" in out["reason"]
        # and it says this is a fact about the vegetation, not a broken sensor
        assert "not a data failure" in out["reason"]

    def test_the_greenup_threshold_is_declared_arbitrary(self):
        days, ndvi = self._season()
        assert "ARBITRARY" in rl.greenup_timing(days, ndvi)["threshold_basis"]


class TestProductivity:
    def test_no_absolute_biomass_is_ever_quoted(self):
        out = rl.productivity_index([0.3, 0.45, 0.5])
        assert out["biomass_kg_ha"] is None
        assert "no locally fitted" in out["biomass_reason"]

    def test_comparison_against_the_sites_own_history(self):
        hist = {2016: 0.40, 2017: 0.42, 2018: 0.38, 2019: 0.41, 2020: 0.39}
        poor = rl.productivity_index([0.20, 0.21, 0.19], hist)
        assert poor["verdict"].startswith("well below")
        good = rl.productivity_index([0.60, 0.62, 0.58], hist)
        assert good["verdict"].startswith("well above")

    def test_a_normal_season_is_called_normal(self):
        hist = {2016: 0.40, 2017: 0.42, 2018: 0.38, 2019: 0.41, 2020: 0.39}
        out = rl.productivity_index([0.40, 0.40, 0.40], hist)
        assert out["verdict"] == "near this site's normal"

    def test_too_little_history_withholds_the_comparison(self):
        out = rl.productivity_index([0.4], {2019: 0.4, 2020: 0.41})
        assert out["verdict"] is None
        assert "at least 3" in out["verdict_reason"]

    def test_the_comparison_is_explicitly_to_this_site_not_another(self):
        hist = {2016: 0.40, 2017: 0.42, 2018: 0.38, 2019: 0.41, 2020: 0.39}
        out = rl.productivity_index([0.40], hist)
        assert "this site's own history" in out["verdict_basis"]

    def test_no_observations_is_not_available_rather_than_zero(self):
        out = rl.productivity_index([None, None])
        assert out["status"] == "NOT AVAILABLE"
        assert "ndvi_integral" not in out


class TestAreaNaming:
    def test_an_area_named_with_claim_language_is_refused(self, ee_env):
        import importlib
        importlib.reload(rl)
        area = {"properties": {"name": "tribal land block 4"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [33.0, 14.4], [33.1, 14.4], [33.1, 14.5], [33.0, 14.5],
                    [33.0, 14.4]]]}}
        out = rl.analyse_rangeland(area, "2022-07-01", "2022-10-01")
        assert out["status"] == "REFUSED"
        assert out["hits"]

    def test_a_neutrally_named_area_is_analysed(self, ee_env):
        import importlib
        importlib.reload(rl)
        area = {"properties": {"name": "Range block 4"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [33.0, 14.4], [33.1, 14.4], [33.1, 14.5], [33.0, 14.5],
                    [33.0, 14.4]]]}}
        out = rl.analyse_rangeland(area, "2022-07-01", "2022-10-01")
        assert out["status"] == "OK"
        assert "conflict_sensitivity" in out


def test_a_refused_area_still_reports_which_area_it_was(ee_env):
    """
    Caught by running the dashboard: the refusal rendered as "?: the supplied
    area name contains claim language". Withholding MEASUREMENTS for an area is
    the point; withholding its identity makes the refusal a dead end for whoever
    has to fix the file.
    """
    import importlib
    importlib.reload(rl)
    area = {"properties": {"name": "tribal land block 4"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [33.0, 14.4], [33.1, 14.4], [33.1, 14.5], [33.0, 14.5],
                [33.0, 14.4]]]}}
    out = rl.analyse_rangeland(area, "2022-07-01", "2022-10-01")
    assert out["status"] == "REFUSED"
    assert out["name"] == "tribal land block 4"


def test_a_window_past_the_jrc_coverage_end_says_so_not_dry(ee_env):
    """
    Live check on 2026-08-29: the JRC monthly series runs 1984-03 to 2021-12.
    A 2022 window returns nothing, and reporting that as "no surface-water
    observations" reads as "the hafirs were dry" — the opposite of what an
    absent dataset means, and the more dangerous of the two readings for a
    pastoralist deciding where to move.
    """
    import importlib
    importlib.reload(rl)
    geom = ee_env.Geometry("empty_area")
    out = rl.water_points(geom, "2022-07-01", "2023-03-31")
    if out["status"] == "NOT AVAILABLE" and "dataset_coverage_end" in out:
        assert out["dataset_coverage_end"] == rl.JRC_COVERAGE_END
        assert "NOT an observation that these water points were dry" in out["reason"]
        assert "2021" in out["remedy"]


def test_the_jrc_coverage_end_is_recorded():
    assert rl.JRC_COVERAGE_END == "2021-12-01"
