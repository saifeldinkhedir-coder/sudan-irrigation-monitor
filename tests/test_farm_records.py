"""
Farm records and the rule-based advisory.

The theme running through these tests is the REPORTED / MEASURED distinction.
Everything the satellite produces can be wrong in ways the data reveals;
everything a person types can be wrong in ways it does not. The store must keep
the two labelled apart, and the advisory must stay silent where the engine was
silent rather than filling the gap with something plausible.
"""

import pytest

import farm_records as fr


@pytest.fixture
def store(tmp_path):
    s = fr.RecordStore(str(tmp_path / "records.db"))
    yield s
    s.close()


# --- operations and costs -----------------------------------------------------

class TestOperations:
    def test_an_unknown_operation_type_is_rejected_at_construction(self):
        with pytest.raises(ValueError):
            fr.Operation(field_id="F1", date="2022-08-01", operation="rain_dance")

    def test_costs_group_by_operation(self, store):
        for op, cost in (("planting", 1000.0), ("fertiliser", 2500.0),
                         ("fertiliser", 500.0), ("harvest", 1200.0)):
            store.add_operation(fr.Operation("F1", "2022-08-01", op, cost=cost))
        out = store.cost_breakdown("F1")
        assert out["status"] == "OK"
        assert out["by_operation"]["fertiliser"] == 3000.0
        assert out["total_cost"] == 5200.0
        assert out["n_operations"] == 4

    def test_costs_are_labelled_reported_not_measured(self, store):
        store.add_operation(fr.Operation("F1", "2022-08-01", "planting", cost=10.0))
        out = store.cost_breakdown("F1")
        assert out["provenance_kind"] == "REPORTED"
        assert "not detectable from the data" in out["caveat"]

    def test_a_field_with_no_records_is_not_available_not_zero(self, store):
        out = store.cost_breakdown("F-nothing")
        assert out["status"] == "NOT AVAILABLE"
        assert "total_cost" not in out

    def test_mixed_currencies_refuse_rather_than_invent_a_rate(self, store):
        store.add_operation(fr.Operation("F1", "2022-08-01", "planting",
                                         cost=100.0, currency="SDG"))
        store.add_operation(fr.Operation("F1", "2022-08-02", "fertiliser",
                                         cost=50.0, currency="USD"))
        out = store.cost_breakdown("F1")
        assert out["status"] == "NOT AVAILABLE"
        assert "exchange rate" in out["reason"]

    def test_the_date_window_is_respected(self, store):
        store.add_operation(fr.Operation("F1", "2021-08-01", "planting", cost=100.0))
        store.add_operation(fr.Operation("F1", "2022-08-01", "planting", cost=200.0))
        out = store.cost_breakdown("F1", start="2022-01-01", end="2022-12-31")
        assert out["total_cost"] == 200.0


class TestGrossMargin:
    def test_costs_without_sales_refuse_to_report_a_margin(self, store):
        store.add_operation(fr.Operation("F1", "2022-08-01", "planting", cost=500.0))
        out = store.gross_margin("F1")
        assert out["status"] == "NOT AVAILABLE"
        assert "masquerading" in out["reason"]
        assert out["total_cost"] == 500.0     # what IS known is still given

    def test_a_complete_record_yields_a_margin(self, store):
        store.add_operation(fr.Operation("F1", "2022-08-01", "planting", cost=500.0))
        store.add_operation(fr.Operation("F1", "2022-09-01", "fertiliser", cost=1500.0))
        store.add_sale(fr.Sale("F1", "2023-01-15", 4.0, "tonne", 6000.0))
        out = store.gross_margin("F1")
        assert out["status"] == "OK"
        assert out["gross_margin"] == 4000.0
        assert out["provenance_kind"] == "REPORTED"

    def test_the_margin_says_it_is_not_comparable_with_satellite_figures(self, store):
        store.add_operation(fr.Operation("F1", "2022-08-01", "planting", cost=1.0))
        store.add_sale(fr.Sale("F1", "2023-01-15", 1.0, "tonne", 2.0))
        assert "not comparable" in store.gross_margin("F1")["caveat"]


class TestWaterProductivity:
    def _stocked(self, store):
        store.add_operation(fr.Operation("F1", "2022-08-01", "planting", cost=1000.0))
        store.add_sale(fr.Sale("F1", "2023-01-15", 4.0, "tonne", 5000.0))

    def test_a_mixed_figure_is_labelled_mixed_and_says_which_half(self, store):
        self._stocked(store)
        out = store.water_productivity("F1", irrigation_requirement_mm=400.0,
                                       field_area_ha=2.0)
        assert out["status"] == "OK"
        assert out["provenance_kind"] == "MIXED"
        assert "REPORTED" in out["provenance_detail"]["numerator"]
        assert "MEASURED" in out["provenance_detail"]["denominator"]

    def test_it_refuses_to_be_called_water_use_efficiency(self, store):
        self._stocked(store)
        out = store.water_productivity("F1", 400.0, 2.0)
        assert "must not be quoted as it" in out["caveat"]
        assert "REQUIRED, not water DELIVERED" in out["caveat"]

    def test_missing_water_requirement_gives_no_figure(self, store):
        self._stocked(store)
        out = store.water_productivity("F1", None, 2.0)
        assert out["status"] == "NOT AVAILABLE"
        assert "margin_per_m3" not in out

    def test_the_volume_conversion_is_right(self, store):
        # 400 mm over 2 ha = 400 * 2 * 10 = 8000 m3
        self._stocked(store)
        out = store.water_productivity("F1", 400.0, 2.0)
        assert out["water_volume_m3"] == 8000.0


# --- the advisory -------------------------------------------------------------

def _field(with_water=True, withheld=False):
    return {
        "name": "Field A",
        "reference_provenance": {"verdict_withheld": withheld},
        "condition": {
            "indicators": {"vigour": {"status": "OK", "value": 0.41}},
            "context": {"rainfall_mm_last_14d": 3.2,
                        "reading_status": "OK",
                        "reading": "no stress signal"}},
        "water_requirement": ({"status": "OK",
                               "irrigation_requirement_mm": 310.0}
                              if with_water else
                              {"status": "NOT AVAILABLE",
                               "reason": "no ERA5-Land data"}),
        "nutrition": {"status": "OK", "claim_level": "relative",
                      "relative_condition": "middle of the scheme"},
    }


class TestAdvisory:
    def test_it_speaks_about_what_was_computed(self):
        a = fr.advisory(_field())
        keys = {i["key"] for i in a["items"]}
        assert {"irrigation", "rainfall", "nutrition"} <= keys

    def test_it_is_silent_and_says_so_where_the_engine_was_silent(self):
        a = fr.advisory(_field(with_water=False))
        assert "irrigation" not in {i["key"] for i in a["items"]}
        assert "irrigation" in {w["key"] for w in a["withheld"]}

    def test_a_withheld_verdict_produces_no_stress_sentence(self):
        a = fr.advisory(_field(withheld=True))
        assert "stress" not in {i["key"] for i in a["items"]}
        assert "stress" in {w["key"] for w in a["withheld"]}

    def test_the_irrigation_sentence_says_need_not_delivery(self):
        a = fr.advisory(_field(), lang="en")
        text = next(i["text"] for i in a["items"] if i["key"] == "irrigation")
        assert "NEED" in text
        assert "not a measurement of what reached you" in text

    def test_a_relative_nutrition_reading_denies_being_a_nitrogen_number(self):
        a = fr.advisory(_field(), lang="en")
        text = next(i["text"] for i in a["items"] if i["key"] == "nutrition")
        assert "not a nitrogen measurement" in text

    def test_it_attributes_nothing(self):
        a = fr.advisory(_field(), canal_record={
            "canal_water": {"status": "OK", "value": 0.42}})
        assert a["attributes_cause"] is False
        joined = " ".join(i["text"] for i in a["items"])
        for word in ("blame", "negligent", "denied", "official"):
            assert word not in joined.lower()

    def test_both_languages_produce_the_same_keys(self):
        ar = fr.advisory(_field(), lang="ar")
        en = fr.advisory(_field(), lang="en")
        assert [i["key"] for i in ar["items"]] == [i["key"] for i in en["items"]]
        assert ar["items"][0]["text"] != en["items"][0]["text"]

    def test_the_advisory_threshold_is_declared_arbitrary(self):
        assert "ARBITRARY" in fr.advisory(_field())["basis"]["note"]

    def test_an_empty_field_record_produces_no_invented_advice(self):
        a = fr.advisory({})
        assert a["items"] == [] or all(i["key"] != "irrigation" for i in a["items"])
        assert a["withheld"]


# --- the advisory must read BOTH engine record shapes -------------------------
#
# Found in the first live farm run: the advisory produced one item and reported
# "no CHIRPS rainfall figure" for a record that carried 228 mm. It only knew the
# network engine's condition.indicators/.context nesting, and the agriculture
# engine uses crop_health.readings with rainfall and thermal as siblings.
# Silently withholding advice the engine had already computed is the exact
# failure mode this platform exists to prevent.

def _agri_record(**over):
    rec = {
        "name": "Field 2",
        "reference_provenance": {"verdict_withheld": False},
        "crop_health": {"readings": {
            "vigour": {"status": "OK", "value": 0.21, "threshold": 0.30},
            "canopy_moisture": {"status": "OK", "value": 0.05,
                                "threshold": 0.12}}},
        "thermal_stress": {"status": "OK", "value": 42.4,
                           "neighbourhood_c": 39.0, "difference_c": 3.4},
        "rainfall": {"season_mm": 228.0, "last_14d_mm": 0.0},
        "water_requirement": {"status": "OK",
                              "irrigation_requirement_mm": 310.0},
        "nutrition": {"status": "OK", "claim_level": "relative",
                      "relative_condition": "WITHIN SCHEME NORM"},
    }
    rec.update(over)
    return rec


class TestAdvisoryAcrossRecordShapes:
    def test_the_agriculture_shape_produces_a_full_advisory(self):
        a = fr.advisory(_agri_record(), lang="en")
        keys = {i["key"] for i in a["items"]}
        assert {"irrigation", "rainfall", "nutrition", "stress", "thermal"} <= keys

    def test_rainfall_is_read_from_the_farm_record_not_reported_missing(self):
        a = fr.advisory(_agri_record(), lang="en")
        assert "rainfall" not in {w["key"] for w in a["withheld"]}
        text = next(i["text"] for i in a["items"] if i["key"] == "rainfall")
        assert "0.0 mm" in text

    def test_indicators_below_the_neighbourhood_threshold_are_named(self):
        a = fr.advisory(_agri_record(), lang="en")
        text = next(i["text"] for i in a["items"] if i["key"] == "stress")
        assert "vigour" in text and "canopy moisture" in text

    def test_a_healthy_field_says_so_rather_than_staying_silent(self):
        rec = _agri_record(crop_health={"readings": {
            "vigour": {"status": "OK", "value": 0.62, "threshold": 0.30}}})
        a = fr.advisory(rec, lang="en")
        text = next(i["text"] for i in a["items"] if i["key"] == "stress")
        assert "no indicator below" in text

    def test_thermal_says_it_leads_the_visible_signal(self):
        a = fr.advisory(_agri_record(), lang="en")
        text = next(i["text"] for i in a["items"] if i["key"] == "thermal")
        assert "3.4 degC warmer" in text
        assert "before visible vigour" in text

    def test_a_small_thermal_difference_raises_nothing(self):
        rec = _agri_record(thermal_stress={"status": "OK", "difference_c": 0.3})
        a = fr.advisory(rec, lang="en")
        assert "thermal" not in {i["key"] for i in a["items"]}

    def test_the_network_shape_still_works(self):
        """The original record shape must not have been broken by the change."""
        a = fr.advisory(_field(), lang="en")
        keys = {i["key"] for i in a["items"]}
        assert {"irrigation", "rainfall", "nutrition"} <= keys

    def test_a_withheld_verdict_still_suppresses_the_stress_item(self):
        rec = _agri_record(reference_provenance={"verdict_withheld": True})
        a = fr.advisory(rec, lang="en")
        assert "stress" not in {i["key"] for i in a["items"]}
        assert "stress" in {w["key"] for w in a["withheld"]}
