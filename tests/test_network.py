"""
Continuity, siltation candidates, efficiency and resolvability.

The recurring theme: an UNOBSERVED reach is not a DRY reach, and a missing
denominator is not a denominator of one. Both mistakes produce a confident
finding out of an absence, which is the failure mode this whole engine is built
against.
"""

import network as nw


# --- continuity ---------------------------------------------------------------

class TestContinuity:
    def test_water_stopping_partway_is_located(self):
        out = nw.continuity_profile([0.60, 0.55, 0.40, 0.02, 0.01, 0.00])
        assert out["status"] == "OK"
        assert out["first_dry_reach"] == 4
        assert out["last_wet_reach"] == 3
        assert out["longest_dry_run"] == 3
        assert out["continuous_to_last_wet"] is True

    def test_a_uniformly_half_wet_canal_is_not_the_same_as_a_broken_one(self):
        """Both average about the same. Only one has a place to go and look."""
        broken = nw.continuity_profile([0.6, 0.6, 0.6, 0.0, 0.0, 0.0])
        uniform = nw.continuity_profile([0.3, 0.3, 0.3, 0.3, 0.3, 0.3])
        assert broken["longest_dry_run"] == 3
        assert uniform["longest_dry_run"] == 0
        assert broken["first_dry_reach"] == 4
        assert uniform["first_dry_reach"] is None

    def test_an_unobserved_reach_is_not_counted_as_dry(self):
        out = nw.continuity_profile([0.6, None, 0.6, 0.0, 0.0])
        assert out["states"][1] == "UNOBSERVED"
        assert out["unobserved_reaches"] == 1
        assert out["dry_reaches"] == 2

    def test_an_unobserved_reach_breaks_a_dry_run_rather_than_extending_it(self):
        out = nw.continuity_profile([0.0, 0.0, None, 0.0, 0.0])
        assert out["longest_dry_run"] == 2      # not 4, and not 5
        assert "not dry reaches" in out["unobserved_note"]

    def test_continuity_is_unknown_when_an_unseen_reach_sits_in_the_span(self):
        out = nw.continuity_profile([0.6, None, 0.6, 0.0])
        assert out["continuous_to_last_wet"] is None

    def test_a_fully_dry_canal_reports_no_wet_reach_rather_than_reach_zero(self):
        out = nw.continuity_profile([0.0, 0.01, 0.0])
        assert out["last_wet_reach"] is None
        assert out["first_dry_reach"] == 1

    def test_all_reaches_unobserved_is_not_available(self):
        out = nw.continuity_profile([None, None, None])
        assert out["status"] == "NOT AVAILABLE"
        assert "usable radar" in out["reason"]

    def test_no_reaches_at_all_is_not_available(self):
        assert nw.continuity_profile([])["status"] == "NOT AVAILABLE"

    def test_the_threshold_is_declared_arbitrary(self):
        out = nw.continuity_profile([0.5, 0.5])
        assert "ARBITRARY" in out["threshold_basis"]

    def test_the_output_never_describes_movement_of_water(self):
        out = nw.continuity_profile([0.6, 0.0, 0.6])
        blob = " ".join(str(v) for v in out.values()).lower()
        assert "flow" not in blob
        assert "flowing" not in blob
        assert "discharge" not in blob


# --- siltation ----------------------------------------------------------------

class TestSiltationCandidates:
    def test_a_declining_reach_is_listed(self):
        out = nw.siltation_candidates({
            3: {2019: 0.60, 2020: 0.58, 2021: 0.20, 2022: 0.18}})
        assert len(out["candidates"]) == 1
        assert out["candidates"][0]["reach"] == 3
        assert out["candidates"][0]["relative_drop"] > 0.25

    def test_a_stable_reach_is_not_listed(self):
        out = nw.siltation_candidates({
            1: {2019: 0.50, 2020: 0.52, 2021: 0.49, 2022: 0.51}})
        assert out["candidates"] == []
        assert out["reaches_examined"] == 1

    def test_too_few_seasons_is_skipped_and_reported_not_judged(self):
        out = nw.siltation_candidates({4: {2021: 0.6, 2022: 0.1}})
        assert out["candidates"] == []
        assert out["reaches_skipped_too_few_seasons"][0]["reach"] == 4
        assert out["reaches_examined"] == 0

    def test_it_is_a_candidate_list_not_a_siltation_finding(self):
        out = nw.siltation_candidates({
            3: {2019: 0.60, 2020: 0.58, 2021: 0.20, 2022: 0.18}})
        assert "worth inspecting" in out["interpretation"]
        assert "none of them is separated here" in out["interpretation"]

    def test_no_sediment_depth_or_volume_is_ever_estimated(self):
        out = nw.siltation_candidates({
            3: {2019: 0.60, 2020: 0.58, 2021: 0.20, 2022: 0.18}})
        assert "not observable from orbit" in out["not_measured"]
        for cand in out["candidates"]:
            assert not any(k in cand for k in ("depth_m", "volume_m3", "sediment"))

    def test_missing_seasons_are_skipped_not_treated_as_zero(self):
        out = nw.siltation_candidates({
            2: {2019: 0.5, 2020: None, 2021: 0.5, 2022: 0.5}})
        assert out["candidates"] == []       # a None must not read as a collapse

    def test_no_data_is_not_available(self):
        assert nw.siltation_candidates({})["status"] == "NOT AVAILABLE"


# --- efficiency: the refusal ---------------------------------------------------

class TestWaterUseEfficiency:
    def test_without_a_release_volume_efficiency_is_refused(self):
        out = nw.water_use_efficiency(et_consumed_mm=700.0, command_area_ha=1200.0)
        assert out["status"] == "OK"          # consumption IS computable
        assert out["efficiency"] is None
        assert out["efficiency_status"] == "NOT AVAILABLE"
        assert "not from a satellite" in out["efficiency_reason"]

    def test_it_refuses_to_substitute_a_design_discharge(self):
        out = nw.water_use_efficiency(700.0, 1200.0)
        assert "design discharge" in out["efficiency_reason"]
        assert "is not substituted here" in out["efficiency_reason"]

    def test_the_consumption_half_is_still_reported(self):
        out = nw.water_use_efficiency(700.0, 1200.0)
        # 700 mm over 1200 ha = 700 * 1200 * 10 = 8,400,000 m3
        assert out["consumed_m3"] == 8400000.0
        assert "consumption, not efficiency" in out["interpretation"]

    def test_with_a_measured_release_the_ratio_is_given_and_labelled_mixed(self):
        out = nw.water_use_efficiency(700.0, 1200.0, water_released_m3=12000000.0)
        assert out["efficiency_status"] == "OK"
        assert out["efficiency"] == 0.7
        assert out["provenance_kind"] == "MIXED"
        assert "REPORTED" in out["provenance_detail"]["denominator"]

    def test_a_ratio_above_one_is_explained_rather_than_clipped(self):
        out = nw.water_use_efficiency(700.0, 1200.0, water_released_m3=4000000.0)
        assert out["efficiency"] > 1.0
        assert "not at a physical impossibility" in out["efficiency_caveat"]

    def test_missing_inputs_give_no_number(self):
        assert nw.water_use_efficiency(None, 1200.0)["efficiency"] is None
        assert nw.water_use_efficiency(700.0, None)["status"] == "NOT AVAILABLE"

    def test_a_zero_release_is_refused_not_divided_by(self):
        out = nw.water_use_efficiency(700.0, 1200.0, water_released_m3=0.0)
        assert out["efficiency"] is None
        assert out["efficiency_status"] == "NOT AVAILABLE"


# --- resolvability -------------------------------------------------------------

class TestResolvability:
    def test_a_wide_canal_is_resolvable(self):
        r = nw.resolvability_note(40.0)
        assert r["resolvable"] is True
        assert r["pixels_across"] == 4.0

    def test_a_gezira_minor_is_flagged_as_mixed_pixel(self):
        r = nw.resolvability_note(8.0)
        assert r["resolvable"] is False
        assert "below one" in r["note"]

    def test_a_marginal_canal_says_the_fraction_is_biased_low(self):
        r = nw.resolvability_note(15.0)
        assert r["resolvable"] is False
        assert "biased low" in r["note"]
        assert "may only mean a narrow canal" in r["note"]

    def test_an_unknown_width_is_not_silently_assumed_resolvable(self):
        r = nw.resolvability_note(None)
        assert r["resolvable"] is None
        assert "unverified" in r["note"]


# --- engine-facing --------------------------------------------------------------

def test_reach_fractions_require_the_caller_to_have_resolved_direction(ee_env):
    """The function takes `reverse` rather than deciding for itself: getting the
    direction wrong would report a break at the wrong end of the canal."""
    import importlib
    importlib.reload(nw)
    import inspect
    assert "reverse" in inspect.signature(nw.reach_wet_fractions).parameters
    assert "reverse" in inspect.signature(nw.canal_continuity).parameters


# --- the stopping point only exists when the pattern supports one -------------
#
# The live New Halfa run on 2026-08-30 printed "water not detected beyond reach
# 0 (3 wet / 2 dry)". Two faults in one line: reach 0 does not exist, and water
# plainly WAS detected after the reach being named. The headline was derived
# from first_dry_reach, which is only the stopping point when the dry reaches
# form a clean tail. On a narrow canal, where each reach is a mixed pixel and
# the wet/dry call flickers, interleaving is the normal case, not an edge case.

class TestContinuityPattern:
    def test_a_clean_stop_names_the_last_wet_reach(self):
        out = nw.continuity_profile([0.6, 0.6, 0.6, 0.0, 0.0, 0.0])
        assert out["pattern"] == "STOPS"
        assert out["water_reaches_to"] == 3

    def test_a_dry_reach_before_wet_ones_is_not_a_stopping_point(self):
        """The exact live case: dry at reach 1, wet afterwards."""
        out = nw.continuity_profile([0.0, 0.6, 0.6, 0.6, 0.0])
        assert out["pattern"] == "INTERMITTENT"
        assert out["water_reaches_to"] is None
        assert out["wet_reaches"] == 3

    def test_wet_dry_wet_is_intermittent_not_a_stop(self):
        out = nw.continuity_profile([0.6, 0.0, 0.6, 0.0])
        assert out["pattern"] == "INTERMITTENT"
        assert out["water_reaches_to"] is None

    def test_a_fully_wet_canal_is_continuous(self):
        out = nw.continuity_profile([0.5] * 6)
        assert out["pattern"] == "CONTINUOUS"
        assert out["water_reaches_to"] is None

    def test_a_fully_dry_canal_reports_no_water(self):
        out = nw.continuity_profile([0.0, 0.01, 0.0])
        assert out["pattern"] == "NO WATER DETECTED"
        assert out["water_reaches_to"] is None

    def test_no_pattern_ever_yields_reach_zero(self):
        for series in ([0.0, 0.6, 0.6], [0.0] * 4, [0.6] * 4,
                       [0.6, 0.0, 0.6], [0.6, 0.6, 0.0]):
            out = nw.continuity_profile(series)
            assert out.get("water_reaches_to") != 0
