"""
Tests for the platform's decision logic: what gets reported, what gets withheld,
and why.

These tests are deliberately about DECISIONS, not arithmetic for its own sake.
Each one pins down a promise from the scientific-integrity rules:

  rule 1  an uncomputable indicator is NOT AVAILABLE, never zero
  rule 2  thresholds are derived from the data, per area, per run
  rule 4  no absolute nitrogen figure without calibration AND an acceptable RMSE
  rule 5  the equity flag fires only on a gap we are confident is real
  rule 8  only clear satellite/ground cases are scored; the rest are UNCLEAR

If someone later "simplifies" the engine in a way that quietly re-introduces a
zero-for-missing, or quotes a nitrogen number from a 12-point model, or flags a
canal on a noisy point estimate, one of these tests goes red.

Run:  pytest -q
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import decision_logic as dl


# ------------------------------------------------------------------ rule 2 ----
class TestRobustThreshold:
    def test_low_tail_is_below_median(self):
        thr = dl.robust_threshold(0.3, 0.5, 0.7, k_sigma=2.0, low_tail=True)
        assert thr == pytest.approx(0.5 - 2.0 * 0.2)

    def test_high_tail_is_above_median(self):
        thr = dl.robust_threshold(0.3, 0.5, 0.7, k_sigma=2.0, low_tail=False)
        assert thr == pytest.approx(0.5 + 2.0 * 0.2)

    def test_missing_percentile_returns_none_not_zero(self):
        # rule 1: a threshold that cannot be computed must be absent, so the
        # caller reports NOT AVAILABLE rather than thresholding against 0.
        assert dl.robust_threshold(None, 0.5, 0.7, 2.0, True) is None

    def test_threshold_moves_with_the_data(self):
        # rule 2: a wider distribution must yield a lower stress threshold; the
        # threshold is a property of THIS area, not a fixed constant.
        narrow = dl.robust_threshold(0.45, 0.5, 0.55, 2.0, True)
        wide = dl.robust_threshold(0.2, 0.5, 0.8, 2.0, True)
        assert wide < narrow


# ------------------------------------------------------------------ defect 2 --
class TestOtsu:
    def test_clean_bimodal_splits_in_the_valley(self):
        # Two well-separated humps: bare soil near 0.15, crop near 0.75.
        mids = [0.05 + 0.1 * i for i in range(10)]          # 0.05..0.95
        counts = [0, 40, 30, 5, 0, 0, 5, 35, 45, 5]
        out = dl.otsu_threshold(counts, mids)
        assert out["threshold"] is not None
        assert 0.3 < out["threshold"] < 0.65
        assert out["is_bimodal"] is True
        assert out["separability"] > 0.5

    def test_unimodal_is_flagged_weak(self):
        # One hump: there is no honest split, and the engine must be able to say
        # so rather than reporting a confident extent from a made-up cut.
        mids = [0.05 + 0.1 * i for i in range(10)]
        counts = [1, 3, 10, 30, 40, 30, 10, 3, 1, 0]
        out = dl.otsu_threshold(counts, mids)
        assert out["is_bimodal"] is False
        # The point of this case: separability stays HIGH on a single hump
        # (splitting a bell at its mean explains lots of variance), which is
        # exactly why separability cannot be the bimodality test. Valley depth
        # is what correctly reads this as one hump.
        assert out["separability"] > 0.5
        assert out["bimodality"] < 0.5

    def test_empty_histogram_not_available(self):
        out = dl.otsu_threshold([], [])
        assert out["threshold"] is None
        assert "NOT AVAILABLE" in out["basis"]


# ------------------------------------------------------------------ rule 5 ----
class TestHeadTailEquity:
    def test_two_reaches_is_insufficient(self):
        # A gap from two points cannot be told from noise: refuse it.
        fit = dl.fit_head_tail_slope([0.0, 1.0], [0.7, 0.4])
        assert fit is None
        flag = dl.equity_flag(fit, flag_threshold=0.2)
        assert flag["status"] == "INSUFFICIENT DATA"
        assert flag["flagged"] is False

    def test_clear_declining_gradient_is_flagged(self):
        pos = [0.0, 0.25, 0.5, 0.75, 1.0]
        vals = [0.72, 0.66, 0.60, 0.52, 0.45]        # ~37% drop, tight
        fit = dl.fit_head_tail_slope(pos, vals)
        assert fit is not None
        assert fit.slope < 0
        assert fit.gap > 0.2
        flag = dl.equity_flag(fit, flag_threshold=0.2)
        assert flag["flagged"] is True
        assert flag["gap_ci"][0] is not None
        # Confidence, not just a point estimate.
        assert flag["gap_ci"][0] <= flag["gap_point_estimate"] <= flag["gap_ci"][1]

    def test_noisy_flat_canal_is_not_flagged(self):
        # No real gradient, just scatter around a flat line. Point estimate may
        # wander, but the CI must straddle the threshold so it does NOT flag.
        pos = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        vals = [0.60, 0.55, 0.63, 0.57, 0.61, 0.58]
        fit = dl.fit_head_tail_slope(pos, vals)
        flag = dl.equity_flag(fit, flag_threshold=0.2)
        assert flag["flagged"] is False

    def test_near_zero_head_gap_is_unreliable_not_flagged(self):
        # Head vigour near zero makes the ratio explode; the engine must call it
        # unreliable and refuse to flag it, not report a -1000% "gap".
        pos = [0.0, 0.25, 0.5, 0.75, 1.0]
        vals = [0.04, 0.03, 0.02, 0.015, 0.01]      # bare soil throughout
        fit = dl.fit_head_tail_slope(pos, vals)
        flag = dl.equity_flag(fit, flag_threshold=0.2)
        assert flag["status"] == "OK"
        assert flag["flagged"] is False
        assert flag["gap_reliable"] is False
        assert "floor" in flag["reason"]

    def test_flag_never_attributes_cause(self):
        pos = [0.0, 0.5, 1.0, 0.25, 0.75]
        vals = [0.7, 0.6, 0.45, 0.65, 0.5]
        flag = dl.equity_flag(dl.fit_head_tail_slope(pos, vals), 0.2)
        assert "attributes nothing" in flag["attribution_caveat"]


# ------------------------------------------------------------------ climate ---
class TestDrySpells:
    def test_longest_run_counts_consecutive_dry_days(self):
        rain = [0, 0, 5, 0, 0, 0, 0, 8, 0]
        assert dl.longest_dry_run(rain, rain_floor_mm=1.0) == 4

    def test_a_trace_below_floor_still_counts_dry(self):
        rain = [0.5, 0.2, 0.9]
        assert dl.longest_dry_run(rain, rain_floor_mm=1.0) == 3

    def test_all_wet_is_zero(self):
        assert dl.longest_dry_run([10, 20, 5], rain_floor_mm=1.0) == 0


class TestStressReading:
    # rule 3: a field mean is compared to a REFERENCE threshold, and no verdict
    # is offered without rainfall context.
    def test_stress_with_little_rain(self):
        out = dl.stress_reading(value=0.30, threshold=0.40, rain_mm=1.0,
                                rain_floor_mm=5.0)
        assert out["stressed"] is True
        assert out["reading"].startswith("STRESS WITH LITTLE RAIN")

    def test_stress_despite_rain_points_away_from_drought(self):
        out = dl.stress_reading(value=0.30, threshold=0.40, rain_mm=40.0,
                                rain_floor_mm=5.0)
        assert out["stressed"] is True
        assert out["reading"].startswith("STRESS DESPITE RAIN")

    def test_no_stress_when_above_reference(self):
        out = dl.stress_reading(value=0.55, threshold=0.40, rain_mm=1.0,
                                rain_floor_mm=5.0)
        assert out["stressed"] is False

    def test_no_threshold_means_no_verdict(self):
        # This is the guard against the old bug: without a reference threshold
        # there must be NO stress verdict, not a threshold the field can never
        # fall below.
        out = dl.stress_reading(value=0.30, threshold=None, rain_mm=1.0,
                                rain_floor_mm=5.0)
        assert out["status"] == "NOT AVAILABLE"
        assert out["stressed"] is None

    def test_no_rain_context_means_no_verdict(self):
        out = dl.stress_reading(value=0.30, threshold=0.40, rain_mm=None,
                                rain_floor_mm=5.0)
        assert out["status"] == "NOT AVAILABLE"
        assert "rainfall context" in out["reason"]


class TestSeasonVsHistory:
    def test_needs_three_history_years(self):
        assert dl.season_percentile_verdict(100.0, [90.0, 110.0]) is None

    def test_dry_season_reads_much_drier(self):
        out = dl.season_percentile_verdict(50.0, [100, 120, 130, 110, 140])
        assert out["verdict"].startswith("MUCH DRIER")
        assert out["percentile"] == 0.0

    def test_normal_season_reads_normal(self):
        out = dl.season_percentile_verdict(115.0, [100, 110, 120, 130, 90])
        assert "normal" in out["verdict"]


# ------------------------------------------------------------------ rule 4 ----
class TestNitrogenLadder:
    def test_relative_always_available_with_spread(self):
        out = dl.relative_condition(0.2, scheme_p25=0.3, scheme_p75=0.6)
        assert out["condition"] == "BELOW SCHEME NORM"

    def test_relative_unavailable_without_spread(self):
        out = dl.relative_condition(0.4, scheme_p25=0.5, scheme_p75=0.5)
        assert out["status"] == "NOT AVAILABLE"

    def test_sufficiency_flags_deficiency_below_strip(self):
        out = dl.sufficiency_reading(0.6, 0.9, deficient_cut=0.90, marginal_cut=0.95)
        assert out["reading"].startswith("LIKELY DEFICIENT")
        assert out["sufficiency_index"] == round(0.6 / 0.9, 3)

    def test_sufficiency_sufficient_near_strip(self):
        out = dl.sufficiency_reading(0.97, 1.0, 0.90, 0.95)
        assert out["reading"].startswith("SUFFICIENT")

    def test_calibration_refused_below_min_points(self):
        # rule 4: 29 points must NOT yield an absolute nitrogen number.
        gate = dl.calibration_gate(29, rmse=0.3, min_points=30, max_rmse=0.6)
        assert gate["may_quote"] is False
        assert "29" in gate["reason"]

    def test_calibration_refused_when_rmse_too_high(self):
        gate = dl.calibration_gate(50, rmse=0.9, min_points=30, max_rmse=0.6)
        assert gate["may_quote"] is False
        assert "RMSE" in gate["reason"] or "rmse" in gate["reason"].lower()

    def test_calibration_allowed_when_both_conditions_met(self):
        gate = dl.calibration_gate(40, rmse=0.4, min_points=30, max_rmse=0.6)
        assert gate["may_quote"] is True

    def test_calibration_refused_when_unfitted(self):
        gate = dl.calibration_gate(40, rmse=None, min_points=30, max_rmse=0.6)
        assert gate["may_quote"] is False


# ------------------------------------------------------------------ rule 8 ----
class TestAgreement:
    def test_both_poor_agree(self):
        assert dl.agreement_verdict(0.2, "wilting", scheme_p25=0.35) == "AGREE"

    def test_both_healthy_agree(self):
        assert dl.agreement_verdict(0.6, "healthy", scheme_p25=0.35) == "AGREE"

    def test_satellite_worse_when_only_satellite_sees_a_problem(self):
        assert dl.agreement_verdict(0.2, "healthy", scheme_p25=0.35) == "SATELLITE_WORSE"

    def test_ground_worse_when_only_observer_sees_a_problem(self):
        assert dl.agreement_verdict(0.6, "wilting", scheme_p25=0.35) == "GROUND_WORSE"

    def test_missing_satellite_is_unclear_not_a_guess(self):
        assert dl.agreement_verdict(None, "wilting", scheme_p25=0.35) == "UNCLEAR"

    def test_missing_ground_is_unclear(self):
        assert dl.agreement_verdict(0.2, "", scheme_p25=0.35) == "UNCLEAR"

    def test_unrecognised_canopy_term_is_unclear(self):
        # An ambiguous free-text value must not be forced into a verdict.
        assert dl.agreement_verdict(0.2, "so-so", scheme_p25=0.35) == "UNCLEAR"


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(["pytest", "-q", __file__]))


class TestCalibrationProgress:
    """
    The gates are the right decision and, alone, a discouraging experience: a
    refusal that cannot say what would lift it is indistinguishable from one
    that never lifts, and the second teaches people to stop collecting data.
    """

    def test_an_empty_set_says_how_many_are_needed(self):
        p = dl.calibration_progress(0, 30, quantity="a yield")
        assert p["points_remaining"] == 30
        assert p["blocked_by"] == "POINTS"
        assert "30 more measurements" in p["next_step"]

    def test_a_partial_set_counts_down(self):
        p = dl.calibration_progress(12, 30)
        assert p["points_remaining"] == 18
        assert "12 of 30" in p["next_step"]
        assert 0 < p["fraction"] < 1

    def test_one_remaining_is_singular(self):
        assert "1 more measurement " in dl.calibration_progress(29, 30)["next_step"]

    def test_enough_points_but_unfitted_says_so(self):
        p = dl.calibration_progress(35, 30, rmse=None, max_rmse=0.25)
        assert p["blocked_by"] == "UNFITTED"
        assert p["unlocked"] is False
        assert "enough to fit" in p["next_step"]

    def test_a_weak_model_explains_the_narrow_range_trap(self):
        p = dl.calibration_progress(35, 30, rmse=0.9, max_rmse=0.25)
        assert p["blocked_by"] == "ERROR"
        assert "narrow range" in p["next_step"]

    def test_a_good_model_unlocks(self):
        p = dl.calibration_progress(35, 30, rmse=0.1, max_rmse=0.25)
        assert p["unlocked"] is True
        assert p["blocked_by"] is None

    def test_the_fraction_never_exceeds_one(self):
        assert dl.calibration_progress(100, 30, 0.1, 0.25)["fraction"] == 1.0
