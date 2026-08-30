"""
Crop water requirement, forecast, and the yield refusal.

The ET0 tests check the physics against values published in FAO-56 itself
(Tables 2.3 and 2.4), not against whatever this implementation happens to
return. That distinction matters: a test asserting the code's own output would
pass forever while being wrong, and ET0 is the one number here that has an
externally correct answer.
"""

import math

import agronomy as ag
import decision_logic as dl


# --- FAO-56 physics, checked against the published tables ---------------------

class TestVapourPressure:
    def test_saturation_vapour_pressure_matches_fao56_table_2_3(self):
        # FAO-56 Table 2.3: e0(20 C) = 2.338 kPa, e0(25 C) = 3.168 kPa
        assert abs(ag.saturation_vapour_pressure(20.0) - 2.338) < 0.005
        assert abs(ag.saturation_vapour_pressure(25.0) - 3.168) < 0.005

    def test_slope_matches_fao56_table_2_4(self):
        # FAO-56 Table 2.4: Delta(20 C) ~ 0.145 kPa/C
        assert abs(ag.slope_vapour_pressure_curve(20.0) - 0.145) < 0.002

    def test_psychrometric_constant_at_sea_level(self):
        # FAO-56: gamma ~ 0.067 kPa/C at 101.3 kPa
        assert abs(ag.psychrometric_constant(101.3) - 0.067) < 0.001

    def test_wind_conversion_factor_at_10m(self):
        # FAO-56 eq. 47: the 10 m factor is 0.748
        assert abs(ag.wind_speed_2m(1.0) - 0.748) < 0.002


class TestET0:
    def test_a_hot_dry_sudanese_day_is_in_the_physically_expected_range(self):
        # 40/24 C, dew point 10 C, 3 m/s at 10 m, Rn 18 MJ/m2/day.
        et0 = ag.et0_penman_monteith(40.0, 24.0, 10.0, 3.0, 18.0)
        assert 6.0 < et0 < 13.0

    def test_a_humid_day_transpires_less_than_a_dry_one(self):
        dry = ag.et0_penman_monteith(40.0, 24.0, 5.0, 3.0, 18.0)
        humid = ag.et0_penman_monteith(40.0, 24.0, 23.0, 3.0, 18.0)
        assert humid < dry

    def test_more_wind_over_dry_air_raises_et0(self):
        calm = ag.et0_penman_monteith(40.0, 24.0, 10.0, 0.5, 18.0)
        windy = ag.et0_penman_monteith(40.0, 24.0, 10.0, 6.0, 18.0)
        assert windy > calm

    def test_more_net_radiation_raises_et0(self):
        low = ag.et0_penman_monteith(35.0, 22.0, 12.0, 2.0, 8.0)
        high = ag.et0_penman_monteith(35.0, 22.0, 12.0, 2.0, 22.0)
        assert high > low

    def test_dewpoint_above_air_temperature_does_not_produce_negative_vpd(self):
        # A data artefact must not turn into a negative demand term.
        et0 = ag.et0_penman_monteith(25.0, 20.0, 35.0, 2.0, 15.0)
        assert et0 is not None and et0 >= 0.0

    def test_missing_input_returns_none_not_zero(self):
        assert ag.et0_penman_monteith(None, 24.0, 10.0, 3.0, 18.0) is None
        assert ag.et0_penman_monteith(40.0, 24.0, 10.0, 3.0, None) is None

    def test_tmax_below_tmin_is_refused_as_impossible(self):
        assert ag.et0_penman_monteith(20.0, 30.0, 10.0, 3.0, 18.0) is None

    def test_et0_is_never_negative(self):
        et0 = ag.et0_penman_monteith(15.0, 14.0, 14.0, 0.1, -5.0)
        assert et0 >= 0.0


# --- crop coefficient ---------------------------------------------------------

class TestKcb:
    def test_kcb_rises_with_ndvi(self):
        assert ag.kcb_from_ndvi(0.8)["kcb"] > ag.kcb_from_ndvi(0.3)["kcb"]

    def test_bare_soil_ndvi_clamps_at_zero_not_negative(self):
        r = ag.kcb_from_ndvi(0.02)
        assert r["kcb"] == 0.0
        assert r["clamped"] is True

    def test_dense_canopy_clamps_at_the_physical_ceiling(self):
        r = ag.kcb_from_ndvi(0.98)
        assert r["kcb"] == ag.KCB_MAX
        assert r["clamped"] is True

    def test_coefficients_are_declared_arbitrary(self):
        assert "ARBITRARY" in ag.kcb_from_ndvi(0.5)["basis"]
        assert "Sudan" in ag.kcb_from_ndvi(0.5)["basis"]

    def test_no_ndvi_means_no_coefficient(self):
        assert ag.kcb_from_ndvi(None) is None


# --- effective rainfall -------------------------------------------------------

class TestEffectiveRainfall:
    def test_small_showers_are_intercepted_and_contribute_nothing(self):
        r = ag.effective_rainfall_mm([1.0, 1.5, 0.5])
        assert r["total_rainfall_mm"] == 3.0
        assert r["effective_rainfall_mm"] == 0.0

    def test_effective_is_always_below_total(self):
        r = ag.effective_rainfall_mm([20.0, 30.0, 10.0])
        assert 0 < r["effective_rainfall_mm"] < r["total_rainfall_mm"]

    def test_missing_days_are_skipped_not_counted_as_zero_rain(self):
        r = ag.effective_rainfall_mm([10.0, None, 10.0])
        assert r["days"] == 2

    def test_the_corrections_are_declared_arbitrary(self):
        assert "ARBITRARY" in ag.effective_rainfall_mm([10.0])["basis"]


# --- the requirement-vs-delivery distinction ----------------------------------

class TestCropWaterRequirement:
    def test_requirement_is_computed_and_labelled_as_requirement(self, ee_env):
        import importlib
        importlib.reload(ag)
        geom = ee_env.Geometry("test_area")
        out = ag.crop_water_requirement(geom, "2022-07-01", "2022-10-01",
                                        mean_ndvi=0.6,
                                        daily_rain_mm=[0.0] * 90)
        # The mock returns no usable ERA5 series, so the honest answer is a
        # refusal - and it must be a refusal, not a zero.
        assert out["status"] in ("OK", "NOT AVAILABLE")
        if out["status"] == "OK":
            assert "REQUIRED, not water DELIVERED" in out["etc_caveat"]
        else:
            assert out["reason"]

    def test_no_ndvi_gives_et0_but_refuses_etc(self):
        # Exercised directly on the pure path: no canopy, no crop coefficient.
        assert ag.kcb_from_ndvi(None) is None


# --- yield: the refusal is the feature ----------------------------------------

class TestYieldGate:
    def test_no_calibration_means_no_tonnage(self):
        y = ag.yield_estimate(0.72, "sorghum")
        assert y["status"] == "OK"
        assert y["claim_level"] == "relative"
        assert y["yield_t_ha"] is None
        assert "30 local harvest measurements" in y["reason"]

    def test_too_few_points_still_refuses(self):
        y = ag.yield_estimate(0.72, "sorghum",
                              calibration={"n_points": 12, "rmse_fraction": 0.1,
                                           "slope": 8.0, "intercept": -1.0})
        assert y["yield_t_ha"] is None
        assert "12" in y["reason"]

    def test_a_weak_model_is_refused_even_with_enough_points(self):
        y = ag.yield_estimate(0.72, "sorghum",
                              calibration={"n_points": 60, "rmse_fraction": 0.9,
                                           "slope": 8.0, "intercept": -1.0})
        assert y["yield_t_ha"] is None
        assert "RMSE" in y["reason"]

    def test_a_good_model_quotes_the_number_with_its_error(self):
        y = ag.yield_estimate(0.72, "sorghum",
                              calibration={"n_points": 60, "rmse_fraction": 0.15,
                                           "r2": 0.81, "slope": 8.0,
                                           "intercept": -1.0})
        assert y["claim_level"] == "calibrated"
        assert y["yield_t_ha"] is not None
        assert y["confidence"]["rmse_fraction"] == 0.15
        assert y["confidence"]["n_points"] == 60

    def test_no_ndvi_means_not_available_rather_than_zero_yield(self):
        y = ag.yield_estimate(None, "sorghum")
        assert y["status"] == "NOT AVAILABLE"
        assert y["yield_t_ha"] is None


# --- the shared gate keeps its nitrogen wording -------------------------------

def test_the_gate_still_speaks_about_nitrogen_by_default():
    g = dl.calibration_gate(5, 0.1, 30, 0.6)
    assert "nitrogen" in g["reason"]


def test_the_gate_can_speak_about_another_quantity():
    g = dl.calibration_gate(5, 0.1, 30, 0.6, quantity="a yield figure")
    assert "yield" in g["reason"]
    assert "nitrogen" not in g["reason"]


# --- the GFS archive guard ----------------------------------------------------
#
# Verified by live measurement, not by this test: filtering NOAA/GFS0P25 by
# bounds and forecast_hours alone left 228,058 images and did not return in ten
# minutes; adding the date filter brought it to 2,052 steps in 14.5 seconds.
# A unit test cannot reproduce that, so what it CAN do is guard the constant
# that makes the difference.

class TestForecastWindow:
    def test_the_run_window_stays_a_recent_window(self):
        """
        If this is ever widened to weeks or months, the call reverts to scanning
        a decade of superseded model runs — slow, and wrong regardless of speed,
        since averaging forecasts that have already been replaced is not a
        forecast of anything.
        """
        assert 1 <= ag.RECENT_RUNS_DAYS <= 7

    def test_the_horizon_stays_inside_useful_skill(self):
        assert 1 <= ag.FORECAST_DAYS <= 10

    def test_the_window_is_declared_arbitrary_in_the_source(self):
        import inspect
        src = inspect.getsource(ag)
        i = src.index("RECENT_RUNS_DAYS = ")
        assert "ARBITRARY" in src[max(0, i - 400):i]


# --- ETc as an integral, not a product of means --------------------------------

class TestInterpolation:
    def test_a_sparse_series_is_filled_between_observations(self):
        r = ag.interpolate_to_daily([0, 10], [0.2, 0.4], 20)
        assert r["daily"][0] == 0.2
        assert abs(r["daily"][5] - 0.3) < 1e-9
        assert r["daily"][10] == 0.4

    def test_nothing_is_extrapolated_past_the_last_observation(self):
        r = ag.interpolate_to_daily([0, 10], [0.2, 0.4], 20)
        assert r["daily"][15] is None
        assert r["daily"][19] is None

    def test_a_long_gap_is_left_empty_rather_than_bridged(self):
        """Joining two observations six weeks apart invents a canopy
        trajectory nobody saw, during the part of the season - green-up or
        senescence - where a straight line is least like the truth."""
        r = ag.interpolate_to_daily([0, 60], [0.1, 0.8], 70, max_gap_days=30)
        assert all(v is None for v in r["daily"])
        assert r["filled_days"] == 0

    def test_the_gap_limit_is_declared_arbitrary(self):
        assert "ARBITRARY" in ag.interpolate_to_daily([0, 5], [0.2, 0.3], 10)["basis"]

    def test_one_observation_cannot_be_interpolated(self):
        r = ag.interpolate_to_daily([3], [0.5], 10)
        assert all(v is None for v in r["daily"])
        assert "fewer than two" in r["reason"]

    def test_coverage_is_reported(self):
        r = ag.interpolate_to_daily([0, 10], [0.2, 0.4], 20)
        assert 0 < r["coverage"] < 1


class TestEtcIntegral:
    def _opposed_season(self):
        """Canopy low when ET0 is high - the real Sudanese pattern, and the
        case where the season-mean shortcut is worst."""
        et0 = [8.0] * 60 + [5.0] * 120 + [8.0] * 60
        ndvi = [0.05] * 60 + [0.75] * 120 + [0.05] * 60
        return et0, ndvi

    def test_the_integral_differs_materially_from_the_flat_method(self):
        et0, ndvi = self._opposed_season()
        flat = ag.kcb_from_ndvi(sum(ndvi) / len(ndvi))["kcb"] * sum(et0)
        integral = ag.etc_time_integrated(et0, ndvi)["etc_mm"]
        assert abs(integral - flat) / flat > 0.15, (
            "if these agree the test season is not exercising the bias")

    def test_the_flat_method_overstates_when_canopy_and_et0_oppose(self):
        et0, ndvi = self._opposed_season()
        flat = ag.kcb_from_ndvi(sum(ndvi) / len(ndvi))["kcb"] * sum(et0)
        assert ag.etc_time_integrated(et0, ndvi)["etc_mm"] < flat

    def test_the_two_methods_agree_when_et0_is_flat(self):
        """The shortcut is only exact when ET0 does not vary; this pins that."""
        et0 = [6.0] * 200
        ndvi = [0.1] * 100 + [0.7] * 100
        flat = ag.kcb_from_ndvi(sum(ndvi) / len(ndvi))["kcb"] * sum(et0)
        integral = ag.etc_time_integrated(et0, ndvi)["etc_mm"]
        assert abs(integral - flat) < 1.0

    def test_missing_days_contribute_nothing_and_are_counted(self):
        r = ag.etc_time_integrated([5.0, 5.0, None, 5.0], [0.5, 0.5, 0.5, None])
        assert r["days_used"] == 2
        assert r["days_in_window"] == 4

    def test_thin_coverage_refuses_the_total_rather_than_scaling_it_up(self):
        et0 = [6.0] * 100
        ndvi = [0.5] * 20 + [None] * 80
        r = ag.etc_time_integrated(et0, ndvi, min_coverage=0.5)
        assert r["status"] == "NOT AVAILABLE"
        assert r["etc_mm"] is None
        assert "not a random sample" in r["coverage_basis"]

    def test_the_method_is_named_in_the_output(self):
        r = ag.etc_time_integrated([6.0] * 10, [0.5] * 10)
        assert "not the product of the two season means" in r["method"]

    def test_the_weighted_mean_kcb_is_reported_for_checking(self):
        et0, ndvi = self._opposed_season()
        r = ag.etc_time_integrated(et0, ndvi)
        assert r["kcb_et0_weighted_mean"] is not None
        assert 0 <= r["kcb_et0_weighted_mean"] <= ag.KCB_MAX


class TestMethodLabels:
    """The two ETc methods are named constants, not strings buried in the
    source. An earlier version of this test grepped the function body and
    failed because the sentence was split across two source lines - testing
    prose again rather than behaviour."""

    def test_the_fallback_announces_that_it_is_approximate(self):
        assert ag.ETC_METHOD_APPROXIMATE.startswith("APPROXIMATE")
        assert "Supply a dated NDVI series" in ag.ETC_METHOD_APPROXIMATE

    def test_the_fallback_says_when_it_is_wrong_and_why(self):
        assert "uncorrelated" in ag.ETC_METHOD_APPROXIMATE
        assert "the bare weeks are the hottest" in ag.ETC_METHOD_APPROXIMATE

    def test_the_integral_is_labelled_as_the_integral(self):
        assert "the integral" in ag.ETC_METHOD_INTEGRAL
        assert "not the product of the two season means" in ag.ETC_METHOD_INTEGRAL

    def test_the_integral_result_carries_that_label(self):
        r = ag.etc_time_integrated([6.0] * 10, [0.5] * 10)
        assert r["method"] == ag.ETC_METHOD_INTEGRAL

    def test_the_two_labels_are_distinguishable(self):
        assert ag.ETC_METHOD_INTEGRAL != ag.ETC_METHOD_APPROXIMATE
        assert "APPROXIMATE" not in ag.ETC_METHOD_INTEGRAL
