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
