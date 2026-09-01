"""
Run-to-run change detection.

The test this module exists to pass: a sorghum crop senescing on schedule in
November must NOT be reported as declining. A change detector that flags every
autumn decline flags every field on the scheme at once, and a tool that cries
wolf at the whole farm buries the one field that is actually failing.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import change as CH


def _rec(name, vigour, threshold=0.30, dates=("2022-10-01",), sigma=None,
         peak_day=None, greenup=30.0, length=140.0, day_offsets=None,
         status="OK"):
    vig = {"status": status, "value": vigour, "threshold": threshold,
           "sensor": "Sentinel-2 median", "scale_m": 10}
    if sigma is not None:
        vig["robust_sigma"] = sigma
    rec = {"name": name,
           "crop_health": {"readings": {"vigour": vig}},
           "series": {"status": "OK", "dates": list(dates)}}
    if day_offsets:
        rec["series"]["day_offsets"] = list(day_offsets)
    if peak_day is not None:
        rec["phenology"] = {"status": "OK", "peak_day": peak_day,
                            "greenup_day": greenup,
                            "season_length_days": length}
    return rec


def _report(fields, generated="2022-11-01T00:00:00+00:00", crop="sorghum"):
    return {"generated_utc": generated, "crop": crop, "fields": fields}


# ==============================================================================
# PHENOLOGY - THE POINT OF THE MODULE
# ==============================================================================

class TestRipeningIsNotFailure:
    def test_a_decline_past_the_peak_is_reported_as_ripening(self):
        """Sorghum peaks in October and senesces on purpose all the way to
        harvest. Flagging that is how a tool loses its reader."""
        prev = _rec("F", 0.60, dates=("2022-10-01",), peak_day=90.0,
                    day_offsets=[90])
        curr = _rec("F", 0.35, dates=("2022-11-15",), peak_day=90.0,
                    day_offsets=[135])
        out = CH.field_change(prev, curr)
        assert out["verdict"] == "EXPECTED SENESCENCE"
        assert out["past_peak"] is True
        assert out["delta"] < 0

    def test_the_same_decline_before_the_peak_is_a_decline(self):
        """The same number, the other verdict. What separates them is the
        green-up date the engine already computed."""
        prev = _rec("F", 0.60, dates=("2022-08-01",), peak_day=120.0,
                    day_offsets=[40])
        curr = _rec("F", 0.35, dates=("2022-08-20",), peak_day=120.0,
                    day_offsets=[60])
        out = CH.field_change(prev, curr)
        assert out["verdict"] == "DECLINED"
        assert out["past_peak"] is False

    def test_no_phenology_does_not_become_still_growing(self):
        """None must not collapse into False. Assuming a field is still
        growing is exactly how ripening gets reported as failure."""
        prev = _rec("F", 0.60, dates=("2022-10-01",))
        curr = _rec("F", 0.35, dates=("2022-11-15",))
        out = CH.field_change(prev, curr)
        assert out["past_peak"] is None
        # With no phenology the tool cannot claim ripening, so it reports the
        # decline - and the reader can see past_peak is unknown.
        assert out["verdict"] == "DECLINED"

    def test_a_rise_past_the_peak_is_still_an_improvement(self):
        prev = _rec("F", 0.35, dates=("2022-11-01",), peak_day=90.0,
                    day_offsets=[120])
        curr = _rec("F", 0.60, dates=("2022-11-15",), peak_day=90.0,
                    day_offsets=[135])
        assert CH.field_change(prev, curr)["verdict"] == "IMPROVED"


# ==============================================================================
# WHAT COUNTS AS A CHANGE
# ==============================================================================

class TestSignificance:
    def test_a_move_inside_the_noise_floor_is_steady(self):
        prev, curr = _rec("F", 0.500), _rec("F", 0.515)
        out = CH.field_change(prev, curr)
        assert out["verdict"] == "STEADY"
        assert out["significant"] is False

    def test_a_noisy_field_needs_a_bigger_move_to_be_believed(self):
        """The same delta, two fields. The one with the wider internal spread
        is not credited with a change."""
        quiet = CH.field_change(_rec("F", 0.50, sigma=0.02),
                                _rec("F", 0.44, sigma=0.02))
        noisy = CH.field_change(_rec("F", 0.50, sigma=0.20),
                                _rec("F", 0.44, sigma=0.20))
        assert quiet["significant"] is True
        assert noisy["significant"] is False

    def test_the_floor_applies_when_no_spread_was_recorded_and_says_so(self):
        out = CH.field_change(_rec("F", 0.50), _rec("F", 0.44))
        assert out["threshold"] == CH.NDVI_FLOOR
        assert "noise floor" in out["judged_against"]
        assert out["judged_against_ar"]

    def test_a_field_with_a_spread_says_it_was_judged_against_itself(self):
        out = CH.field_change(_rec("F", 0.50, sigma=0.02),
                              _rec("F", 0.44, sigma=0.02))
        assert "own spread" in out["judged_against"]


class TestUnmeasuredIsNotSteady:
    def test_a_field_the_satellite_lost_is_not_comparable(self):
        """The rule the whole platform runs on. "We could not see it" must not
        become "it did not change"."""
        prev = _rec("F", 0.50)
        curr = _rec("F", None, status="NO DATA")
        out = CH.field_change(prev, curr)
        assert out["verdict"] == "NOT COMPARABLE"
        assert out["significant"] is False
        assert "later run" in out["reason"]
        assert out["reason_ar"]

    def test_a_field_that_was_unmeasured_before_is_also_not_comparable(self):
        out = CH.field_change(_rec("F", None, status="NO DATA"), _rec("F", 0.5))
        assert out["verdict"] == "NOT COMPARABLE"
        assert "earlier run" in out["reason"]


# ==============================================================================
# DATES
# ==============================================================================

class TestDatesAreSceneDatesNotRunDates:
    def test_the_gap_is_measured_between_the_observations(self):
        """Two runs a week apart can rest on scenes a month apart, because the
        newer run may have found nothing but cloud. Reporting seven days for a
        thirty-one day gap makes a slow drift look like a collapse."""
        prev = _rec("F", 0.50, dates=("2022-09-01", "2022-10-01"))
        curr = _rec("F", 0.44, dates=("2022-10-01", "2022-11-01"))
        out = CH.field_change(prev, curr)
        assert out["from_date"] == "2022-10-01"
        assert out["to_date"] == "2022-11-01"
        assert out["gap_days"] == 31

    def test_a_missing_scene_date_gives_no_gap_rather_than_zero(self):
        prev = _rec("F", 0.50, dates=())
        out = CH.field_change(prev, _rec("F", 0.44))
        assert out["gap_days"] is None


# ==============================================================================
# THE WHOLE-FARM COMPARISON
# ==============================================================================

class TestCompare:
    def _pair(self):
        prev = _report([_rec("A", 0.60, peak_day=120.0, day_offsets=[40]),
                        _rec("B", 0.50),
                        _rec("C", 0.20)])
        curr = _report([_rec("A", 0.25, peak_day=120.0, day_offsets=[60]),
                        _rec("B", 0.51),
                        _rec("C", 0.45)])
        return prev, curr

    def test_every_shared_field_is_compared(self):
        out = CH.compare(*self._pair())
        assert out["n_compared"] == 3
        assert {c["name"] for c in out["changes"]} == {"A", "B", "C"}

    def test_declines_are_listed_first(self):
        out = CH.compare(*self._pair())
        assert out["changes"][0]["name"] == "A"
        assert out["changes"][0]["verdict"] == "DECLINED"

    def test_a_field_crossing_its_own_threshold_is_called_out(self):
        """Crossing the threshold is a different event from moving, and it is
        the one that changes what a farmer does."""
        out = CH.compare(*self._pair())
        crossings = {c["name"]: (c["from"], c["to"]) for c in out["crossings"]}
        assert crossings["A"] == ("ok", "attention")
        assert crossings["C"] == ("attention", "ok")
        assert "B" not in crossings

    def test_the_within_farm_rank_is_not_treated_as_a_change(self):
        """A field's rank among its neighbours moves when OTHER fields move.
        That is not a change in this field, so the comparison uses only the
        field's own threshold."""
        assert CH._status_of(_rec("X", 0.40, threshold=0.30)) == "ok"

    def test_a_field_present_in_only_one_run_is_listed_not_dropped(self):
        prev = _report([_rec("A", 0.5), _rec("gone", 0.5)])
        curr = _report([_rec("A", 0.5), _rec("new", 0.5)])
        out = CH.compare(prev, curr)
        assert out["new_fields"] == ["new"]
        assert out["dropped_fields"] == ["gone"]
        assert out["n_compared"] == 1

    def test_the_counts_add_up_to_the_fields_compared(self):
        out = CH.compare(*self._pair())
        assert sum(out["counts"].values()) == out["n_compared"]

    def test_the_note_explains_both_refusals_in_both_languages(self):
        out = CH.compare(*self._pair())
        for text in (out["note"], out["note_ar"]):
            assert text
        assert "ripening" in out["note"]
        assert "نضجًا لا ضررًا" in out["note_ar"]


class TestHeadline:
    def test_it_counts_the_verdicts(self):
        prev = _report([_rec("A", 0.60, peak_day=120.0, day_offsets=[40])])
        curr = _report([_rec("A", 0.30, peak_day=120.0, day_offsets=[60])])
        cmp = CH.compare(prev, curr)
        assert "1 declined before peak" in CH.headline(cmp)
        assert "تراجعت قبل الذروة" in CH.headline(cmp, ar=True)

    def test_nothing_in_common_says_so_rather_than_reporting_zeros(self):
        cmp = CH.compare(_report([_rec("A", 0.5)]), _report([_rec("B", 0.5)]))
        assert "nothing" in CH.headline(cmp)
        assert "لا حقل مشترك" in CH.headline(cmp, ar=True)
