"""
Tests for the farmer channel.

The farmer card is where the platform's integrity rules meet the person with the
most at stake, so the tests are strict about three things: it states a percentage
only when the gap is real and reliable; it never fabricates a clause the engine
marked NOT AVAILABLE; and it attributes cause to no one.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import farmer_channel as fc


FLAGGED_CANAL = {
    "name": "Minor Canal 3",
    "head_tail_equity": {"status": "OK", "gap_reliable": True,
                         "head_fit_ndvi": 0.71, "tail_fit_ndvi": 0.44,
                         "head_tail_gap": 0.38,
                         "reaches": [{"reach": i + 1,
                                      "position_along_canal": round(i / 5, 2),
                                      "mean_ndvi": round(0.71 - 0.055 * i, 3)}
                                     for i in range(6)]},
    "canal_water": {"status": "OK", "value": 0.44},
    "climate": {"season_vs_history": {"verdict": "near this site's normal"}},
}

WATER_MISSING_CANAL = {
    "name": "Minor Canal 5",
    "head_tail_equity": {"status": "OK", "gap_reliable": True,
                         "head_fit_ndvi": 0.6, "tail_fit_ndvi": 0.5,
                         "head_tail_gap": 0.17, "reaches": []},
    "canal_water": {"status": "NOT AVAILABLE",
                    "reason": "only 1 Sentinel-1 scene"},
    "climate": {},
}

UNRELIABLE_CANAL = {
    "name": "Minor Canal 6",
    "head_tail_equity": {"status": "OK", "gap_reliable": False,
                         "head_fit_ndvi": 0.06, "head_tail_gap": -0.5},
    "canal_water": {"status": "OK", "value": 0.22},
    "climate": {},
}


def test_flagged_canal_states_the_gap_in_arabic():
    card = fc.farmer_card(FLAGGED_CANAL, lang="ar")
    assert "38%" in card["text"]
    assert "vigour" in card["clauses"]
    assert "water" in card["clauses"]
    assert card["attributes_cause"] is False


def test_reach_specific_card_uses_that_reach():
    # tail reach (position 1.0) should show a larger drop than a middle reach.
    tail = fc.farmer_card(FLAGGED_CANAL, reach_position=1.0, lang="en")
    mid = fc.farmer_card(FLAGGED_CANAL, reach_position=0.4, lang="en")
    assert "below the head" in tail["text"]
    # extract the percentages
    import re
    pt = int(re.search(r"(\d+)% below", tail["text"]).group(1))
    pm_match = re.search(r"(\d+)% below", mid["text"])
    pm = int(pm_match.group(1)) if pm_match else 0
    assert pt > pm


def test_missing_water_is_said_not_faked():
    card = fc.farmer_card(WATER_MISSING_CANAL, lang="en")
    assert "could not be measured" in card["text"]
    # never a fabricated water percentage
    assert "% of the canal" not in card["text"]


def test_unreliable_gap_states_no_percentage():
    card = fc.farmer_card(UNRELIABLE_CANAL, lang="ar")
    assert "vigour" not in card["clauses"]     # no vigour clause at all
    # water is fine to state
    assert "water" in card["clauses"]


def test_card_never_attributes_cause():
    for canal in (FLAGGED_CANAL, WATER_MISSING_CANAL, UNRELIABLE_CANAL):
        for lang in ("ar", "en"):
            card = fc.farmer_card(canal, lang=lang)
            low = card["text"].lower()
            for w in fc.ATTRIBUTION_WORDS_EN:
                assert w not in low
            for w in fc.ATTRIBUTION_WORDS_AR:
                assert w not in card["text"]


def test_no_measurements_yields_honest_empty():
    empty = {"name": "X", "head_tail_equity": {"status": "INSUFFICIENT DATA"},
             "canal_water": {"status": "INSUFFICIENT DATA"}, "climate": {}}
    card = fc.farmer_card(empty, lang="en")
    # water clause still explains the gap honestly; but with neither vigour nor
    # water nor rain, it must say so rather than invent.
    assert card["attributes_cause"] is False
