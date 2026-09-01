"""
Search, filters and map selection.

The theme running through this file is the one the rest of the app already
obeys in colour: a thing that was never recorded must not be presented as a
thing that failed a test. A filter is where that rule is easiest to break,
because a field dropped from a list of forty leaves no trace at all - nobody
counts a list they did not write.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "farmer_app"))
import search as S
import view as D  # noqa: F401  (imported by search; keeps the path honest)


# ------------------------------------------------------------------ fixtures

def _square(lon, lat, name, size=0.005, **props):
    return {"type": "Feature",
            "properties": {"name": name, **props},
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon, lat], [lon + size, lat], [lon + size, lat + size],
                [lon, lat + size], [lon, lat]]]}}


def _field(name, vigour=0.5, threshold=0.3, greenup=30.0, length=120.0,
           status="OK", phen=True, dates=("2022-07-01", "2022-11-01")):
    rec = {"name": name,
           "crop_health": {"readings": {"vigour": {
               "status": status, "value": vigour, "threshold": threshold,
               "sensor": "Sentinel-2 median", "scale_m": 10}}},
           "series": {"status": "OK", "dates": list(dates)}}
    rec["phenology"] = ({"status": "OK", "greenup_day": greenup,
                         "season_length_days": length, "peak_day": 60.0}
                        if phen else {"status": "NO DATA",
                                      "reason": "flat curve"})
    return rec


def _report(fields, crop="sorghum"):
    return {"season": {"start": "2022-07-01", "end": "2023-03-31"},
            "crop": crop, "n_fields": len(fields), "fields": fields}


REPORT = _report([_field("Field 1", 0.20, 0.30),           # attention
                  _field("Field 2", 0.55),                 # ok
                  _field("Field 3", 0.60, phen=False),     # ok, no phenology
                  _field("Field 4", None, status="NO DATA")])  # unmeasured

FC = {"type": "FeatureCollection", "features": [
    _square(33.10, 14.42, "Field 1"),
    _square(33.14, 14.45, "Field 2", crop="wheat"),
    _square(33.18, 14.48, "Field 3"),
    _square(33.22, 14.51, "Field 4"),
]}


def _index():
    return S.field_index(REPORT, FC)


# ------------------------------------------------------------- normalisation

class TestArabicSearchWorksForArabicTypists:
    def test_the_taa_marbuta_does_not_hide_a_field(self):
        """Someone typing "الجزيره" must find "الجزيرة". They are the same word
        and only one of them is on most phone keyboards."""
        assert S.normalise("الجزيره") == S.normalise("الجزيرة")

    def test_the_alef_forms_are_the_same_letter_for_search(self):
        assert S.normalise("أحمد") == S.normalise("احمد")
        assert S.normalise("إبراهيم") == S.normalise("ابراهيم")

    def test_arabic_indic_digits_find_western_ones(self):
        """A field named "Field 3" must be reachable by typing ٣."""
        assert S.normalise("٣") == "3"
        idx = _index()
        assert [r["name"] for r in
                S.filter_fields(idx, text="٣")["matched"]] == ["Field 3"]

    def test_case_and_spacing_do_not_matter(self):
        assert S.normalise("  FIELD   1 ") == "field 1"

    def test_none_normalises_to_empty_rather_than_raising(self):
        assert S.normalise(None) == ""


# -------------------------------------------------------------------- index

class TestTheIndex:
    def test_every_field_in_the_report_is_indexed(self):
        assert [r["name"] for r in _index()] == \
            ["Field 1", "Field 2", "Field 3", "Field 4"]

    def test_a_field_declaring_its_own_crop_beats_the_farm_default(self):
        """A report run for sorghum over a farm with a wheat block must not
        label the wheat sorghum."""
        by = {r["name"]: r for r in _index()}
        assert by["Field 2"]["crop"] == "wheat"
        assert by["Field 2"]["crop_source"] == "field"
        assert by["Field 1"]["crop"] == "sorghum"
        assert by["Field 1"]["crop_source"] == "farm"

    def test_greenup_is_a_date_derived_from_the_season_start(self):
        by = {r["name"]: r for r in _index()}
        assert by["Field 1"]["greenup_date"] == "2022-07-31"   # +30 days
        assert by["Field 1"]["greenup_source"] == "SATELLITE"

    def test_a_field_with_no_phenology_has_no_derived_dates(self):
        """Not a date of zero, not the season start - no date."""
        by = {r["name"]: r for r in _index()}
        assert by["Field 3"]["greenup_date"] is None
        assert by["Field 3"]["harvest_date"] is None

    def test_an_expected_harvest_is_labelled_estimated(self):
        by = {r["name"]: r for r in _index()}
        assert by["Field 1"]["harvest_date"] == "2022-11-28"   # +30 +120
        assert by["Field 1"]["harvest_source"] == "ESTIMATED"
        assert by["Field 1"]["harvested"] is False

    def test_a_recorded_harvest_is_labelled_reported_and_wins(self):
        """A date the farmer wrote down and a date read off an NDVI curve are
        both dates. Treating them as one kind of fact is how a satellite guess
        ends up in somebody's records as an observation."""
        idx = S.field_index(REPORT, FC, harvests={"Field 1": "2022-12-05"})
        by = {r["name"]: r for r in idx}
        assert by["Field 1"]["harvest_date"] == "2022-12-05"
        assert by["Field 1"]["harvest_source"] == "REPORTED"
        assert by["Field 1"]["harvested"] is True

    def test_the_area_is_computed_from_the_polygon(self):
        by = {r["name"]: r for r in _index()}
        assert by["Field 1"]["area_ha"] > 20

    def test_the_status_matches_the_map(self):
        by = {r["name"]: r for r in _index()}
        assert by["Field 1"]["status"] == "attention"
        assert by["Field 4"]["status"] == "unmeasured"

    def test_crops_present_are_offered_once_each_and_sorted(self):
        assert S.crops_in(_index()) == ["sorghum", "wheat"]

    def test_the_counts_agree_with_the_statuses(self):
        c = S.status_counts(_index())
        assert c["attention"] == 1 and c["unmeasured"] == 1
        assert sum(c.values()) == 4


# ------------------------------------------------------------------ filters

class TestTextAndStatus:
    def test_a_name_search_narrows_the_list(self):
        r = S.filter_fields(_index(), text="Field 2")
        assert [x["name"] for x in r["matched"]] == ["Field 2"]
        assert r["n_total"] == 4 and r["n_matched"] == 1

    def test_a_crop_name_typed_into_the_search_box_finds_its_fields(self):
        r = S.filter_fields(_index(), text="wheat")
        assert [x["name"] for x in r["matched"]] == ["Field 2"]

    def test_no_criteria_returns_everything(self):
        r = S.filter_fields(_index())
        assert r["n_matched"] == 4 and r["active"] == []

    def test_the_status_filter_selects_the_field_to_walk_to(self):
        r = S.filter_fields(_index(), statuses=["attention"])
        assert [x["name"] for x in r["matched"]] == ["Field 1"]
        assert "status" in r["active"]

    def test_unmeasured_is_a_status_you_can_filter_for(self):
        """Asking "which fields could the satellite not see" is a real
        question, and the answer is a work list, not an error."""
        r = S.filter_fields(_index(), statuses=["unmeasured"])
        assert [x["name"] for x in r["matched"]] == ["Field 4"]


class TestUnknownIsNotTheSameAsNoMatch:
    def test_a_field_with_no_crop_is_set_aside_not_dropped(self):
        idx = S.field_index(_report([_field("A")], crop=None), None)
        r = S.filter_fields(idx, crops=["sorghum"])
        assert r["matched"] == []
        assert [x["name"] for x in r["unknown"]] == ["A"]
        assert r["unknown"][0]["unknown_because"] == "crop"

    def test_a_field_of_a_different_crop_is_a_plain_non_match(self):
        """Wheat is not sorghum. That is a genuine answer, not missing data,
        and it must not be reported as uncertainty."""
        r = S.filter_fields(_index(), crops=["sorghum"])
        assert "Field 2" not in [x["name"] for x in r["matched"]]
        assert "Field 2" not in [x["name"] for x in r["unknown"]]

    def test_a_field_with_no_greenup_date_is_set_aside_by_a_date_filter(self):
        r = S.filter_fields(_index(), date_from="2022-07-01",
                            date_to="2022-08-31")
        names = [x["name"] for x in r["matched"]]
        assert "Field 1" in names
        # Field 3 has no phenology, so it has no green-up date to compare.
        assert [x["name"] for x in r["unknown"]] == ["Field 3"]

    def test_every_field_is_accounted_for(self):
        """matched + unknown + genuine non-matches must equal the total. A
        field that is in none of the three has gone missing silently, which is
        the failure this whole module exists to prevent."""
        idx = _index()
        r = S.filter_fields(idx, crops=["sorghum"], date_from="2022-07-01")
        seen = {x["name"] for x in r["matched"]} | \
               {x["name"] for x in r["unknown"]}
        assert seen <= {x["name"] for x in idx}
        assert r["n_matched"] == len(r["matched"])
        assert r["n_unknown"] == len(r["unknown"])
        assert r["n_total"] == 4


class TestDates:
    def test_a_date_window_selects_on_the_chosen_date(self):
        """The same field is in or out depending on WHICH date you filter by,
        so the choice is explicit rather than guessed."""
        idx = _index()
        early = S.filter_fields(idx, date_field="greenup_date",
                                date_from="2022-07-01", date_to="2022-08-31")
        late = S.filter_fields(idx, date_field="harvest_date",
                               date_from="2022-07-01", date_to="2022-08-31")
        assert "Field 1" in [x["name"] for x in early["matched"]]
        assert "Field 1" not in [x["name"] for x in late["matched"]]

    def test_an_open_ended_window_works_from_one_side(self):
        r = S.filter_fields(_index(), date_from="2023-01-01")
        assert r["matched"] == []
        assert "date" in r["active"]

    def test_a_date_object_is_accepted_as_well_as_a_string(self):
        import datetime
        r = S.filter_fields(_index(), date_from=datetime.date(2022, 7, 1))
        assert r["n_matched"] >= 1

    def test_an_unparseable_date_is_ignored_rather_than_matching_nothing(self):
        r = S.filter_fields(_index(), date_from="not a date")
        assert r["n_matched"] == 4


class TestHarvest:
    def test_only_a_reported_harvest_counts_as_harvested(self):
        idx = S.field_index(REPORT, FC, harvests={"Field 2": "2022-12-05"})
        r = S.filter_fields(idx, harvest="harvested")
        assert [x["name"] for x in r["matched"]] == ["Field 2"]

    def test_with_nothing_reported_the_question_is_unanswerable_for_everyone(self):
        """An expected harvest date is this tool's arithmetic, not a report
        from the field, and nothing in the satellite record says "this has been
        cut". So with no reports the honest answer is four unknowns, not four
        noes."""
        idx = S.field_index(REPORT, FC)
        r = S.filter_fields(idx, harvest="harvested")
        assert r["matched"] == []
        assert r["n_unknown"] == 4
        assert all(x["unknown_because"] == "harvest" for x in r["unknown"])

    def test_the_other_option_claims_only_that_nothing_was_reported(self):
        """It is not called "standing". A field cut last week and never
        written down would sit in it."""
        idx = S.field_index(REPORT, FC, harvests={"Field 2": "2022-12-05"})
        r = S.filter_fields(idx, harvest="not_reported")
        assert "Field 2" not in [x["name"] for x in r["matched"]]
        assert "Field 1" in [x["name"] for x in r["matched"]]


# --------------------------------------------------- selection by drawing

class TestPolygonSelection:
    BOX = {"type": "Polygon", "coordinates": [[
        [33.09, 14.41], [33.16, 14.41], [33.16, 14.47], [33.09, 14.47],
        [33.09, 14.41]]]}

    def test_fields_whose_centre_falls_inside_are_selected(self):
        r = S.filter_fields(_index(), polygon=self.BOX)
        assert [x["name"] for x in r["matched"]] == ["Field 1", "Field 2"]
        assert "area" in r["active"]

    def test_a_field_with_no_boundary_cannot_be_placed_and_says_so(self):
        """A field the report knows about but the GeoJSON does not has no
        centroid. It is unknown to a spatial question, not outside the box."""
        idx = S.field_index(REPORT, {"type": "FeatureCollection",
                                     "features": [_square(33.10, 14.42,
                                                          "Field 1")]})
        r = S.filter_fields(idx, polygon=self.BOX)
        assert [x["name"] for x in r["matched"]] == ["Field 1"]
        assert {x["name"] for x in r["unknown"]} == {"Field 2", "Field 3",
                                                     "Field 4"}

    def test_the_polygon_filter_combines_with_the_others(self):
        r = S.filter_fields(_index(), polygon=self.BOX, statuses=["attention"])
        assert [x["name"] for x in r["matched"]] == ["Field 1"]


class TestClickingTheMap:
    def test_a_click_inside_a_field_selects_it(self):
        assert S.field_at_point(_index(), 14.4225, 33.1025) == "Field 1"

    def test_a_click_on_bare_ground_selects_nothing(self):
        """Not the nearest field. "Nearest" quietly becomes "wrong" at the edge
        of a scheme, and a farmer would be reading another tenant's numbers
        under their own field's name."""
        assert S.field_at_point(_index(), 14.90, 33.90) is None

    def test_a_missing_click_is_not_an_error(self):
        assert S.field_at_point(_index(), None, None) is None
