"""
The scheme hierarchy, the run history, and the backup.

These three are the difference between a program that analyses fields and a
system an institution can run for a season. The tests here are mostly about
what each of them refuses to do: aggregate over fields it could not see,
compare two runs over different farms, and call a copy on the same disk a
backup.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import registry as REG
import runs as RUNS
import backup as BK


# ==============================================================================
# THE HIERARCHY
# ==============================================================================

def _props(**kw):
    return kw


class TestTheHierarchyIsData:
    def test_a_farm_with_no_hierarchy_is_a_valid_deployment(self):
        """Most users outside a scheme are this. It is not a degraded case."""
        assert REG.FLAT.depth() == 0
        assert REG.FLAT.path({"anything": 1}) == []
        assert REG.FLAT.placed({}) is True

    def test_the_default_carries_a_warning_in_its_own_name(self):
        """Administrative naming has changed with successive reorganisations,
        and the wrong label on the right structure looks correct in a report."""
        assert "CONFIRM" in REG.GEZIRA.name

    def test_a_deployment_can_define_its_own_levels(self):
        h = REG.Hierarchy([("scheme", "المشروع", "scheme"),
                           ("plot", "القطعة", "plot")])
        assert h.depth() == 2
        assert h.path({"scheme": "Rahad", "plot": "7"}) == ["Rahad", "7"]

    def test_levels_are_bilingual(self):
        assert REG.GEZIRA.label("block", ar=True) == "القسم"
        assert REG.GEZIRA.label("block") == "block"

    def test_an_unknown_level_returns_its_key_rather_than_vanishing(self):
        assert REG.GEZIRA.label("nonesuch") == "nonesuch"


class TestPlacement:
    def test_a_fully_placed_field_has_a_complete_path(self):
        p = _props(group="Wad Habouba", block="14", number="3", tenancy="221")
        assert REG.GEZIRA.placed(p) is True
        assert REG.GEZIRA.path_string(p) == "Wad Habouba / 14 / 3 / 221"

    def test_a_missing_level_stops_the_path_rather_than_being_skipped(self):
        """A field that declares a tenancy but no block cannot be placed under
        a block, and inventing "unknown block" would put every such field into
        one imaginary unit that then appears in reports as a real one."""
        p = _props(group="Wad Habouba", tenancy="221")
        assert REG.GEZIRA.path(p) == ["Wad Habouba"]
        assert REG.GEZIRA.placed(p) is False

    def test_an_empty_string_counts_as_missing(self):
        assert REG.GEZIRA.path(_props(group="G", block="")) == ["G"]

    def test_the_group_key_is_the_path_down_to_that_level(self):
        """Block 14 in one group and block 14 in another are different blocks.
        Grouping on the bare number merges two real places into one row that
        describes neither."""
        a = _props(group="Wad Habouba", block="14", number="1", tenancy="1")
        b = _props(group="Hosh", block="14", number="1", tenancy="1")
        assert REG.GEZIRA.group_key(a, "block") != REG.GEZIRA.group_key(b, "block")

    def test_a_field_that_never_reaches_the_level_has_no_key(self):
        assert REG.GEZIRA.group_key(_props(group="G"), "block") is None


class TestValidationBeforeARun:
    def _fc(self, *props):
        return {"features": [{"properties": p} for p in props]}

    def test_a_fully_placed_file_is_ready(self):
        fc = self._fc(_props(name="A", group="G", block="1", number="2",
                             tenancy="3"))
        out = REG.validate_placement(fc, REG.GEZIRA)
        assert out["ready"] is True and out["fully_placed"] == ["A"]

    def test_a_partially_placed_field_says_where_it_stopped(self):
        fc = self._fc(_props(name="A", group="G", block="1"))
        out = REG.validate_placement(fc, REG.GEZIRA)
        assert out["partially_placed"][0]["reached"] == "number"

    def test_a_flat_deployment_has_nothing_to_validate(self):
        out = REG.validate_placement(self._fc(_props(name="A")), REG.FLAT)
        assert out["ready"] is True

    def test_an_unplaced_field_is_not_refused_only_unrollable(self):
        """Nothing is refused for this. A field with no block still gets
        analysed; it simply cannot be rolled up."""
        fc = self._fc(_props(name="A"))
        out = REG.validate_placement(fc, REG.GEZIRA)
        assert out["ready"] is False
        assert "still get analysed" in out["note"]
        assert out["note_ar"]


# ==============================================================================
# AGGREGATION
# ==============================================================================

def _field(name, vigour, threshold=0.30, **props):
    rec = {"name": name, "properties": props}
    rec["crop_health"] = {"readings": {"vigour": {
        "status": "OK" if vigour is not None else "NO DATA",
        "value": vigour, "threshold": threshold}}}
    return rec


class TestAggregation:
    def _report(self, fields):
        return {"fields": fields}

    def test_fields_roll_up_to_their_block(self):
        r = self._report([
            _field("a", 0.5, group="G", block="1", number="1", tenancy="1"),
            _field("b", 0.4, group="G", block="1", number="1", tenancy="2"),
            _field("c", 0.6, group="G", block="2", number="1", tenancy="1")])
        out = REG.aggregate(r, REG.GEZIRA, "block")
        assert out["n_units"] == 2
        by = {u["key"]: u for u in out["units"]}
        assert by["G / 1"]["n_fields"] == 2
        assert by["G / 1"]["mean_vigour"] == 0.45

    def test_a_unit_that_could_not_be_seen_withholds_its_mean(self):
        """Forty fields of which six could not be seen produce a number that
        describes thirty-four. Calling it the block's is the map's grey problem
        one level up."""
        r = self._report([
            _field("a", 0.5, group="G", block="1", number="1", tenancy="1"),
            _field("b", None, group="G", block="1", number="1", tenancy="2"),
            _field("c", None, group="G", block="1", number="1", tenancy="3")])
        u = REG.aggregate(r, REG.GEZIRA, "block")["units"][0]
        assert u["withheld"] is True
        assert u["mean_vigour"] is None
        assert u["coverage"] < 0.6
        assert "would describe the measured ones" in u["reason"]
        assert u["reason_ar"]

    def test_good_coverage_reports_the_mean_and_the_coverage_both(self):
        r = self._report([
            _field("a", 0.5, group="G", block="1", number="1", tenancy="1"),
            _field("b", 0.5, group="G", block="1", number="1", tenancy="2"),
            _field("c", None, group="G", block="1", number="1", tenancy="3")])
        u = REG.aggregate(r, REG.GEZIRA, "block")["units"][0]
        assert u["withheld"] is False
        assert u["coverage"] == 0.667
        assert u["n_unmeasured"] == 1

    def test_units_with_fields_below_threshold_come_first(self):
        r = self._report([
            _field("a", 0.10, group="G", block="1", number="1", tenancy="1"),
            _field("b", 0.90, group="G", block="2", number="1", tenancy="1")])
        out = REG.aggregate(r, REG.GEZIRA, "block")
        assert out["units"][0]["key"] == "G / 1"
        assert out["units"][0]["n_attention"] == 1

    def test_an_unplaceable_field_is_listed_not_dropped(self):
        r = self._report([_field("a", 0.5, group="G", block="1", number="1",
                                 tenancy="1"),
                          _field("orphan", 0.5)])
        out = REG.aggregate(r, REG.GEZIRA, "block")
        assert [u["name"] for u in out["unplaced"]] == ["orphan"]
        assert "no block recorded" in out["unplaced"][0]["reason"]

    def test_the_coverage_floor_is_labelled_arbitrary(self):
        out = REG.aggregate({"fields": []}, REG.GEZIRA, "block")
        assert "ARBITRARY" in out["basis"]


# ==============================================================================
# THE RUN STORE
# ==============================================================================

def _report_file(tmp_path, name, fields, generated="2022-11-01T00:00:00+00:00"):
    p = tmp_path / name
    p.write_text(json.dumps({
        "generated_utc": generated, "crop": "sorghum",
        "season": {"start": "2022-07-01", "end": "2023-03-31"},
        "n_fields": len(fields),
        "fields": [{"name": f} for f in fields]}), encoding="utf-8")
    return str(p)


class TestTheRunStore:
    def test_a_recorded_run_is_copied_not_referenced(self, tmp_path):
        """A run indexed by path stops existing the moment somebody tidies
        their desktop, and the history is the one thing here that cannot be
        recomputed."""
        store = RUNS.RunStore(str(tmp_path / "runs"))
        src = _report_file(tmp_path, "r1.json", ["A", "B"])
        entry = store.record("Gezira block 14", src)
        os.remove(src)
        assert os.path.exists(store.path_of("Gezira block 14", entry["id"]))
        assert store.load("Gezira block 14", entry["id"])["n_fields"] == 2

    def test_the_index_records_what_the_run_rested_on(self, tmp_path):
        store = RUNS.RunStore(str(tmp_path / "runs"))
        fields = tmp_path / "f.geojson"
        fields.write_text('{"type":"FeatureCollection","features":[]}',
                          encoding="utf-8")
        e = store.record("F", _report_file(tmp_path, "r.json", ["A"]),
                         fields_path=str(fields))
        assert e["fields_digest"]
        assert e["field_names"] == ["A"]

    def test_runs_come_back_in_order(self, tmp_path):
        store = RUNS.RunStore(str(tmp_path / "runs"))
        a = store.record("F", _report_file(tmp_path, "a.json", ["A"]))
        # Same-second stamps would collide; the id is the ordering key.
        store._write_manifest(
            os.path.join(str(tmp_path / "runs"), "F"),
            {"farm": "F", "runs": [{**a, "id": "20220101T000000Z"},
                                   {**a, "id": "20220201T000000Z"}]})
        assert store.latest("F")["id"] == "20220201T000000Z"
        assert store.previous("F")["id"] == "20220101T000000Z"

    def test_one_run_is_not_a_comparison_and_says_so(self, tmp_path):
        store = RUNS.RunStore(str(tmp_path / "runs"))
        store.record("F", _report_file(tmp_path, "a.json", ["A"]))
        out = store.pair_for_comparison("F")
        assert out["ok"] is False
        assert "nothing to compare" in out["reason"]
        assert out["reason_ar"]

    def test_no_runs_at_all_is_not_an_error(self, tmp_path):
        store = RUNS.RunStore(str(tmp_path / "runs"))
        assert store.pair_for_comparison("nobody")["ok"] is False
        assert store.runs("nobody") == []
        assert store.farms() == []

    def test_two_runs_over_different_farms_are_refused(self, tmp_path):
        """A comparison full of "new" and "missing" fields means nothing, and
        the reader will take it as churn on their own land."""
        store = RUNS.RunStore(str(tmp_path / "runs"))
        a = {"field_names": ["A", "B", "C", "D"]}
        b = {"field_names": ["W", "X", "Y", "Z"]}
        out = store.comparable(a, b)
        assert out["ok"] is False
        assert "two different farms" in out["reason"]
        assert out["reason_ar"]

    def test_two_runs_over_one_farm_are_accepted(self, tmp_path):
        store = RUNS.RunStore(str(tmp_path / "runs"))
        out = store.comparable({"field_names": ["A", "B", "C", "D"]},
                               {"field_names": ["A", "B", "C", "E"]})
        assert out["ok"] is True and out["n_shared"] == 3

    def test_a_redrawn_boundary_file_is_flagged_but_not_refused(self, tmp_path):
        """Fields get redrawn. That is not a reason to refuse a comparison, but
        it IS a reason to say so, because a "change" in a redrawn field is
        partly the redrawing."""
        store = RUNS.RunStore(str(tmp_path / "runs"))
        out = store.comparable(
            {"field_names": ["A", "B"], "fields_digest": "aaa"},
            {"field_names": ["A", "B"], "fields_digest": "bbb"})
        assert out["ok"] is True
        assert out["boundaries_changed"] is True

    def test_a_farm_name_cannot_escape_the_store_directory(self, tmp_path):
        store = RUNS.RunStore(str(tmp_path / "runs"))
        e = store.record("../../etc/passwd", _report_file(tmp_path, "r.json",
                                                          ["A"]))
        made = store.path_of("../../etc/passwd", e["id"])
        assert os.path.abspath(made).startswith(
            os.path.abspath(str(tmp_path / "runs")))


# ==============================================================================
# BACKUP
# ==============================================================================

class TestBackup:
    def _make(self, tmp_path):
        import sqlite3
        db = tmp_path / "calibration.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE points (id INTEGER, v REAL)")
        conn.executemany("INSERT INTO points VALUES (?,?)",
                         [(i, i * 1.5) for i in range(30)])
        conn.commit()
        conn.close()
        obs = tmp_path / "observations"
        obs.mkdir()
        (obs / "a.jpg").write_bytes(b"not really a jpeg")
        return str(tmp_path)

    def test_the_survey_counts_what_would_be_lost(self, tmp_path):
        """"You have 30 weighed harvests and 1 photograph" is what makes
        somebody actually carry the file off the machine."""
        s = BK.survey(self._make(tmp_path))
        found = {f["file"]: f for f in s["found"]}
        assert found["calibration.db"]["rows"]["points"] == 30
        assert s["n_photographs"] == 1

    def test_the_survey_names_what_is_deliberately_not_backed_up(self, tmp_path):
        s = BK.survey(self._make(tmp_path))
        assert "still in orbit" in s["note"]
        assert s["note_ar"]

    def test_an_archive_round_trips_and_verifies(self, tmp_path):
        src = self._make(tmp_path)
        dest = str(tmp_path / "out" / "backup.zip")
        made = BK.create(dest, src)
        assert made["n_files"] >= 2
        assert BK.verify(dest)["ok"] is True

    def test_a_corrupted_archive_fails_verification(self, tmp_path):
        """A truncated copy of a season of records looks exactly like a good
        one until the day it is needed."""
        import zipfile
        src = self._make(tmp_path)
        dest = str(tmp_path / "b.zip")
        BK.create(dest, src)
        bad = str(tmp_path / "bad.zip")
        with zipfile.ZipFile(dest) as z, zipfile.ZipFile(bad, "w") as o:
            for n in z.namelist():
                data = z.read(n)
                if n.endswith("calibration.db"):
                    data = data[:-20] + b"corrupted-tail-bytes"
                o.writestr(n, data)
        out = BK.verify(bad)
        assert out["ok"] is False
        assert "calibration.db" in out["corrupt"]

    def test_a_foreign_zip_is_not_pronounced_good(self, tmp_path):
        import zipfile
        p = str(tmp_path / "foreign.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("hello.txt", "hi")
        out = BK.verify(p)
        assert out["ok"] is False
        assert "no manifest" in out["reason"]

    def test_every_archive_says_a_copy_here_is_not_a_backup(self, tmp_path):
        """A backup nobody moves off the machine is a filing habit."""
        made = BK.create(str(tmp_path / "b.zip"), self._make(tmp_path))
        assert "not this laptop" in made["warning_ar"] or made["warning_ar"]
        assert "same machine" in made["warning"]
        assert made["manifest"]["warning_ar"]

    def test_a_missing_store_is_reported_not_silently_skipped(self, tmp_path):
        s = BK.survey(str(tmp_path))
        assert {m["file"] for m in s["missing"]} >= {"farm_records.db"}
