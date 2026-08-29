"""
Tests for the GeoLibre bridge (two-way data flow).

Pins down: a valid submission is scored and stored; a submission missing a
required field is rejected before it touches the store; agreement is UNCLEAR when
the satellite record is absent (never guessed); and the reliability figure
accumulates only from clear cases.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "geolibre_plugin"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import bridge
import nutrition_climate_ground as ncg


def _form(**over):
    base = {"field_id": "F1", "canopy_condition": "wilting",
            "water_reached_field": False, "lat": 15.3, "lon": 35.8,
            "observed_at": "2022-11-01", "photo_path": "/x.jpg",
            "crop": "sorghum", "observer": "ranger1"}
    base.update(over)
    return base


def test_missing_required_field_is_rejected(tmp_path):
    store = ncg.ObservationStore(os.path.join(tmp_path, "o.db"))
    res = bridge.submit_observation(_form(canopy_condition=""), store)
    assert res["ok"] is False
    assert "canopy_condition" in res["missing"]
    store.close()


def test_valid_submission_scored_agree(tmp_path):
    store = ncg.ObservationStore(os.path.join(tmp_path, "o.db"))
    # satellite says poor (NDVI below scheme p25), observer says wilting -> AGREE
    prov = lambda lat, lon, date: {"NDVI": 0.20, "CIre": 1.5}
    res = bridge.submit_observation(_form(), store, satellite_provider=prov,
                                    scheme_p25=0.35)
    assert res["ok"] is True
    assert res["agreement"] == "AGREE"
    summ = store.agreement_summary("sorghum")
    assert summ["available"] is True and summ["total"] == 1
    store.close()


def test_absent_satellite_is_unclear_not_guessed(tmp_path):
    store = ncg.ObservationStore(os.path.join(tmp_path, "o.db"))
    prov = lambda lat, lon, date: None            # no imagery for that date
    res = bridge.submit_observation(_form(), store, satellite_provider=prov,
                                    scheme_p25=0.35)
    assert res["agreement"] == "UNCLEAR"
    # UNCLEAR observations must not enter the reliability rate
    summ = store.agreement_summary("sorghum")
    assert summ["available"] is False
    store.close()


def test_satellite_worse_when_only_satellite_sees_a_problem(tmp_path):
    store = ncg.ObservationStore(os.path.join(tmp_path, "o.db"))
    prov = lambda lat, lon, date: {"NDVI": 0.20}
    res = bridge.submit_observation(_form(canopy_condition="healthy"), store,
                                    satellite_provider=prov, scheme_p25=0.35)
    assert res["agreement"] == "SATELLITE_WORSE"
    store.close()


def test_farmer_card_served_from_results(tmp_path):
    results_path = os.path.join(os.path.dirname(__file__), "..", "docs",
                                "sample_results.json")
    card = bridge.farmer_card_for(results_path, "Minor Canal 3",
                                  reach_position=1.0, lang="en")
    assert "below the head" in card["text"]
    assert card["attributes_cause"] is False


def test_farmer_card_unknown_canal_errors_cleanly():
    results_path = os.path.join(os.path.dirname(__file__), "..", "docs",
                                "sample_results.json")
    card = bridge.farmer_card_for(results_path, "No Such Canal")
    assert "error" in card
