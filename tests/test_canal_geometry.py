"""
Canal geometry ingest and validation.

This checker sits at the point where hand-traced geometry enters the engine, and
its job is to turn every defect that would later become an indefensible number
into an error at ingest, where fixing it is cheap. The direction check is the
one that matters most: it is the difference between a report that points at the
tail of a canal and one that points at the head with equal confidence.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "geometry"))
import canal_geometry as cg


def _canal(coords=None, **props):
    base = {"name": "Minor 7", "vertex_order": "head_first", "width_m": 25}
    base.update(props)
    base = {k: v for k, v in base.items() if v is not None}
    return {"type": "Feature", "properties": base,
            "geometry": {"type": "LineString", "coordinates": coords or [
                [33.00, 14.40], [33.02, 14.41], [33.04, 14.42],
                [33.06, 14.43], [33.08, 14.44]]}}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


# --- geometry helpers ---------------------------------------------------------

class TestLengths:
    def test_a_known_span_measures_about_right(self):
        # 0.01 deg of latitude is about 1.111 km
        L = cg.line_length_m([[33.0, 14.40], [33.0, 14.41]])
        assert 1090 < L < 1130

    def test_a_single_point_has_no_length(self):
        assert cg.line_length_m([[33.0, 14.4]]) == 0.0

    def test_repeated_vertices_are_counted(self):
        assert cg.repeated_vertices(
            [[33.0, 14.4], [33.0, 14.4], [33.1, 14.4]]) == 1


# --- the direction requirement ------------------------------------------------

class TestDirectionRequirement:
    def test_a_canal_with_no_direction_is_an_error(self):
        r = cg.validate_canal_feature(_canal(vertex_order=None))
        assert any("SIGNED" in e for e in r["errors"])

    def test_vertex_order_satisfies_it(self):
        r = cg.validate_canal_feature(_canal(vertex_order="tail_first"))
        assert r["errors"] == []
        assert r["info"]["direction_source"] == "vertex_order"

    def test_an_offtake_satisfies_it(self):
        r = cg.validate_canal_feature(
            _canal(vertex_order=None, offtake=[32.99, 14.40]))
        assert r["errors"] == []
        assert r["info"]["direction_source"] == "offtake"

    def test_a_bad_vertex_order_value_is_rejected(self):
        r = cg.validate_canal_feature(_canal(vertex_order="downstream"))
        assert any("expected 'head_first'" in e for e in r["errors"])

    def test_a_malformed_offtake_is_rejected(self):
        r = cg.validate_canal_feature(
            _canal(vertex_order=None, offtake=[32.99]))
        assert any("expected [lon, lat]" in e for e in r["errors"])


# --- geometry defects ---------------------------------------------------------

class TestGeometryDefects:
    def test_two_vertices_cannot_be_split_into_reaches(self):
        r = cg.validate_canal_feature(
            _canal(coords=[[33.0, 14.4], [33.2, 14.6]]))
        assert any("analytically useless" in e for e in r["errors"])

    def test_a_multilinestring_is_refused_with_the_reason(self):
        f = _canal()
        f["geometry"] = {"type": "MultiLineString",
                         "coordinates": [[[33.0, 14.4], [33.1, 14.5]]]}
        r = cg.validate_canal_feature(f)
        assert any("no unambiguous order" in e for e in r["errors"])

    def test_a_polygon_is_refused(self):
        f = _canal()
        f["geometry"] = {"type": "Polygon", "coordinates": [[[33.0, 14.4]]]}
        r = cg.validate_canal_feature(f)
        assert any("needs a LineString" in e for e in r["errors"])

    def test_a_very_short_line_is_refused(self):
        r = cg.validate_canal_feature(_canal(coords=[
            [33.0000, 14.4000], [33.0002, 14.4001],
            [33.0004, 14.4002], [33.0006, 14.4003]]))
        assert any("not a splittable canal reach" in e for e in r["errors"])

    def test_projected_coordinates_are_caught(self):
        r = cg.validate_canal_feature(_canal(coords=[
            [500000, 1600000], [500100, 1600100],
            [500200, 1600200], [500300, 1600300]]))
        assert any("projected CRS" in e for e in r["errors"])

    def test_coordinates_outside_sudan_warn_about_swapped_axes(self):
        r = cg.validate_canal_feature(_canal(coords=[
            [14.40, 33.00], [14.41, 33.02], [14.42, 33.04], [14.43, 33.06]]))
        assert any("axes may be swapped" in w for w in r["warnings"])

    def test_repeated_vertices_warn(self):
        r = cg.validate_canal_feature(_canal(coords=[
            [33.00, 14.40], [33.00, 14.40], [33.04, 14.42],
            [33.06, 14.43], [33.08, 14.44]]))
        assert any("repeated consecutive vertices" in w for w in r["warnings"])

    def test_an_implausibly_long_canal_warns_but_does_not_block(self):
        r = cg.validate_canal_feature(_canal(coords=[
            [22.0, 4.0], [26.0, 9.0], [30.0, 14.0], [34.0, 19.0]]))
        assert any("unusually long" in w for w in r["warnings"])
        assert not any("unusually long" in e for e in r["errors"])


# --- properties the engine depends on -----------------------------------------

class TestProperties:
    def test_an_unnamed_canal_cannot_be_matched_to_a_command_area(self):
        r = cg.validate_canal_feature(_canal(name=None))
        assert any("matched to canals by name" in e for e in r["errors"])

    def test_a_missing_width_warns_about_the_reliability_qualifier(self):
        r = cg.validate_canal_feature(_canal(width_m=None))
        assert any("below reliable detection" in w for w in r["warnings"])

    def test_a_clean_canal_passes_with_nothing_to_say(self):
        r = cg.validate_canal_feature(_canal())
        assert r["errors"] == []
        assert r["warnings"] == []


# --- collection-level ---------------------------------------------------------

class TestCollection:
    def test_duplicate_names_silently_merge_command_areas_so_they_are_refused(self):
        rep = cg.validate_collection(_fc(_canal(name="Minor 7"),
                                         _canal(name="Minor 7")))
        assert rep["ok"] is False
        assert any("belongs to neither" in e for e in rep["collection_errors"])

    def test_an_empty_file_is_refused(self):
        rep = cg.validate_collection(_fc())
        assert rep["ok"] is False

    def test_no_command_areas_warns_about_withheld_verdicts(self):
        rep = cg.validate_collection(_fc(_canal()))
        assert rep["ok"] is True
        assert any("withhold every stress verdict" in w
                   for w in rep["collection_warnings"])

    def test_unmatched_command_areas_are_reported_as_synthetic_fallback(self):
        commands = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"canal": "Minor 9"},
             "geometry": {"type": "Polygon", "coordinates": [[[33, 14]]]}}]}
        rep = cg.validate_collection(_fc(_canal(name="Minor 7")), commands)
        assert any("SYNTHETIC" in w for w in rep["collection_warnings"])

    def test_a_matched_command_area_produces_no_warning(self):
        commands = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"canal": "Minor 7"},
             "geometry": {"type": "Polygon", "coordinates": [[[33, 14]]]}}]}
        rep = cg.validate_collection(_fc(_canal(name="Minor 7")), commands)
        assert not any("SYNTHETIC" in w for w in rep["collection_warnings"])

    def test_the_report_counts_errors_and_warnings(self):
        rep = cg.validate_collection(_fc(_canal(vertex_order=None),
                                         _canal(name="Minor 8", width_m=None)))
        assert rep["n_errors"] >= 1
        assert rep["n_warnings"] >= 1


# --- OSM ingest ---------------------------------------------------------------

class TestOSM:
    def _payload(self):
        return {"elements": [
            {"type": "way", "id": 42,
             "tags": {"waterway": "canal", "name": "Gezira Main Canal",
                      "name:ar": "الترعة الرئيسية"},
             "geometry": [{"lon": 33.0, "lat": 14.4}, {"lon": 33.1, "lat": 14.5},
                          {"lon": 33.2, "lat": 14.6}, {"lon": 33.3, "lat": 14.7}]},
            {"type": "way", "id": 43, "tags": {"waterway": "drain"},
             "geometry": [{"lon": 33.0, "lat": 14.4}]},          # too short
            {"type": "node", "id": 44},                           # not a way
        ]}

    def test_ways_become_linestring_features(self):
        fc = cg.osm_to_featurecollection(self._payload())
        assert len(fc["features"]) == 1
        f = fc["features"][0]
        assert f["geometry"]["type"] == "LineString"
        assert f["properties"]["name"] == "Gezira Main Canal"
        assert f["properties"]["osm_id"] == 42

    def test_arabic_names_are_preserved(self):
        fc = cg.osm_to_featurecollection(self._payload())
        assert fc["features"][0]["properties"]["osm_name_ar"] == "الترعة الرئيسية"

    def test_an_unnamed_way_gets_a_traceable_identifier(self):
        fc = cg.osm_to_featurecollection({"elements": [
            {"type": "way", "id": 99, "tags": {"waterway": "canal"},
             "geometry": [{"lon": 33.0, "lat": 14.4}, {"lon": 33.1, "lat": 14.5}]}
        ]})
        assert fc["features"][0]["properties"]["name"] == "osm_way_99"

    def test_fetched_canals_carry_no_direction_and_therefore_fail_validation(self):
        """The refusal is intentional. An inherited arbitrary direction is worse
        than an absent one, because it looks like information."""
        fc = cg.osm_to_featurecollection(self._payload())
        f = fc["features"][0]
        assert "vertex_order" not in f["properties"]
        assert "offtake" not in f["properties"]
        assert "mapping artefact" in f["properties"]["direction_note"]
        rep = cg.validate_collection(fc)
        assert rep["ok"] is False

    def test_the_overpass_query_asks_for_the_right_tags_and_bbox_order(self):
        q = cg.build_overpass_query((32.9, 14.2, 33.4, 14.7), ("canal",))
        assert '"waterway"="canal"' in q
        # Overpass wants south,west,north,east - not the bbox order we take in
        assert "(14.2,32.9,14.7,33.4)" in q
        assert "out geom;" in q
