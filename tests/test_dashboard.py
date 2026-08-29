"""
Tests for the dashboard data logic - the manager phrasing must preserve the
engine's integrity guarantees all the way to the screen.

Run against the shaped sample results file, which deliberately contains every
state: a flagged canal, a NOT AVAILABLE canal-water, a weak-bimodality extent,
and a detected-but-unflagged gap.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
import data as D

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "docs", "sample_results.json")


def _results():
    with open(SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def test_sample_loads_and_has_canals():
    r = _results()
    rows = D.canal_rows(r)
    assert len(rows) == len(r["canals"]) > 0


def test_not_available_water_is_worded_not_zeroed():
    rows = D.canal_rows(_results())
    na = [r for r in rows if r["water_status"] == "NOT AVAILABLE"]
    assert na, "sample should contain a NOT AVAILABLE canal-water case"
    for r in na:
        assert r["water_display"] == "not available"
        assert r["water_display"] != "0"        # never a zero standing in for missing


def test_flagged_canal_sorts_to_top():
    rows = D.sort_canals(D.canal_rows(_results()), by="gap")
    assert rows[0]["flagged"] is True
    assert D.flagged_count(rows) >= 1


def test_flagged_row_shows_a_confidence_interval():
    rows = D.canal_rows(_results())
    flagged = [r for r in rows if r["flagged"]][0]
    assert "…" in flagged["gap_ci_display"]      # a CI, not a bare number


def test_weak_extent_is_marked_unreliable():
    rows = D.canal_rows(_results())
    weak = [r for r in rows if r["extent_reliable"] is False]
    assert weak, "sample should contain a weak-bimodality extent case"


def test_reach_series_never_fabricates_when_unavailable():
    # Build a canal whose equity is INSUFFICIENT DATA.
    canal = {"head_tail_equity": {"status": "INSUFFICIENT DATA",
                                  "reason": "fewer than three reaches"}}
    rs = D.reach_series(canal)
    assert rs["available"] is False
    assert "three" in rs["reason"]


def test_nutrition_never_promotes_relative_to_absolute():
    canal = {"nutrition": {"status": "OK", "claim_level": "relative",
                           "relative_condition": "WITHIN SCHEME NORM",
                           "caveat": "..."}}
    out = D.nutrition_summary(canal)
    assert out["claim_level"] == "relative"
    assert "Relative condition" in out["headline"]
    assert "%" not in out["headline"] or "Leaf N" not in out["headline"]


def test_provenance_lines_render_observed_fraction():
    ind = {"provenance": {"sensor": "Sentinel-1", "date_start": "2022-07-01",
                          "date_end": "2023-03-31", "n_scenes": 5,
                          "observed_fraction": 0.82}}
    lines = D.provenance_lines(ind)
    assert any("Area observed: 82%" in l for l in lines)


# --- field layer --------------------------------------------------------------
#
# The field table's one job that the canal table does not have: keeping
# "measured, no threshold" visibly distinct from "measured and fine".

def _field_results(withheld: bool):
    return {"field_geometry_supplied": True, "fields": [{
        "name": "Field A",
        "reference_provenance": {
            "verdict_withheld": withheld,
            "reference_source": ("NOT AVAILABLE: no candidate reference was wide "
                                 "enough relative to the field" if withheld
                                 else "REAL: command area containing the field"),
            "area_ratio": None if withheld else 96.4},
        "condition": {
            "indicators": {
                "vigour": {"status": "OK", "value": 0.42,
                           "threshold": None if withheld else 0.31},
                "canopy_moisture": {"status": "OK", "value": 0.18},
                "thermal_stress": {"status": "NOT AVAILABLE"}},
            "context": {"rainfall_mm_last_14d": 0.4,
                        "reading": None if withheld else "no stress detected"}},
    }]}


def test_absent_field_layer_is_reported_as_absent_not_empty():
    assert D.field_rows({"fields": []}) == []
    assert D.fields_without_verdict([]) == 0


def test_withheld_verdict_never_reads_as_healthy():
    row = D.field_rows(_field_results(withheld=True))[0]
    assert row["verdict"] == "no verdict"
    assert row["verdict_withheld"] is True
    assert "NOT AVAILABLE" in row["verdict_reason"]
    # the measurement itself is still shown - it was genuinely made
    assert row["vigour_display"] == "0.420"
    assert D.fields_without_verdict([row]) == 1


def test_a_real_reference_produces_a_real_verdict():
    row = D.field_rows(_field_results(withheld=False))[0]
    assert row["verdict"] == "no stress detected"
    assert row["verdict_withheld"] is False
    assert row["reference_ratio"] == 96.4
    assert D.fields_without_verdict([row]) == 0


def test_unavailable_indicator_says_so_rather_than_showing_a_number():
    row = D.field_rows(_field_results(withheld=False))[0]
    assert row["thermal_display"] == "not available"


# --- water requirement, rangeland, forecast -----------------------------------

def test_water_requirement_headline_says_needed_not_supplied():
    s = D.water_requirement_summary({"water_requirement": {
        "status": "OK", "et0_mm": 1400.0, "etc_mm": 980.0, "kcb": 0.7,
        "irrigation_requirement_mm": 810.0,
        "etc_caveat": "This is water REQUIRED, not water DELIVERED."}})
    assert s["available"] is True
    assert "needed" in s["headline"]
    assert "beyond rainfall" in s["headline"]
    assert "REQUIRED" in s["caveat"]


def test_water_requirement_absent_is_a_reason_not_a_zero():
    s = D.water_requirement_summary({"water_requirement": {
        "status": "NOT AVAILABLE", "reason": "no ERA5-Land data"}})
    assert s["available"] is False
    assert s["reason"] == "no ERA5-Land data"
    assert "etc_mm" not in s


def test_a_refused_rangeland_area_is_shown_refused_not_dropped():
    rows = D.rangeland_rows({"rangeland": [
        {"status": "REFUSED", "name": "x", "reason": "claim language"},
        {"status": "OK", "name": "Range block A",
         "productivity": {"status": "OK", "ndvi_integral": 0.42,
                          "verdict": "near this site's normal"},
         "timing": {"status": "OK", "greenup_day": 40.0},
         "water_points": {"status": "OK", "water_frequency": 0.55}}]})
    assert len(rows) == 2
    assert rows[0]["status"] == "REFUSED"
    assert rows[0]["reason"] == "claim language"
    assert rows[1]["verdict"] == "near this site's normal"
    assert rows[1]["water"] == "55%"


def test_no_rangeland_means_no_rows_rather_than_an_empty_claim():
    assert D.rangeland_rows({}) == []


def test_forecast_always_carries_its_resolution_caveat():
    f = D.forecast_summary({"forecast": {
        "status": "OK", "horizon_days": 7, "mean_temperature_c": 38.4,
        "mean_precipitation_mm_per_step": 0.1,
        "provenance": {"note": "A ~28 km global model."}}})
    assert f["available"] is True
    assert "28 km" in f["caveat"]


def test_forecast_unavailable_is_reported_as_such():
    f = D.forecast_summary({"forecast": {"status": "NOT AVAILABLE",
                                         "reason": "no GFS steps"}})
    assert f["available"] is False
    assert f["reason"] == "no GFS steps"


def test_the_sample_still_exercises_every_display_state():
    """
    The sample file is a FIXTURE, hand-shaped to contain one of each state the
    dashboard must render: a flagged canal, an unmeasurable canal water figure,
    a weak Otsu split, a field with a verdict and a field without one. It was
    once flattened by regenerating it from a mock run, which silently removed
    those states and left the display paths untested. This test makes that
    flattening fail loudly instead.
    """
    r = _results()
    canals = r["canals"]
    assert any((c.get("head_tail_equity") or {}).get("flagged") for c in canals)
    assert any((c.get("canal_water") or {}).get("status") != "OK" for c in canals)

    frows = D.field_rows(r)
    assert frows, "the sample must contain fields"
    assert any(row["verdict_withheld"] for row in frows)
    assert any(not row["verdict_withheld"] for row in frows)

    assert any(x.get("status") == "REFUSED" for x in r.get("rangeland", [])), \
        "the sample must contain a rangeland area refused for claim language"
