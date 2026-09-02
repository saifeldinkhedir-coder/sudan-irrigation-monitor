"""
Resuming a long run.

The failure being defended against is not exotic: an Earth Engine run over a
scheme is thousands of round trips over an unreliable connection against a
finite quota, and it dies at field 3,700 of 4,000. The tests here are mostly
about the OTHER risk that resuming introduces - quietly merging results from a
different question, which produces a report that is wrong rather than absent.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import checkpoint as CP


def _fc(*names, side=0.004):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": n},
         "geometry": {"type": "Polygon", "coordinates": [[
             [33.1, 14.4], [33.1 + side, 14.4], [33.1 + side, 14.4 + side],
             [33.1, 14.4 + side], [33.1, 14.4]]]}} for n in names]}


class TestTheFingerprint:
    def test_the_same_question_fingerprints_the_same(self):
        a = CP.fingerprint(_fc("A", "B"), 2022, "sorghum", True)
        b = CP.fingerprint(_fc("A", "B"), 2022, "sorghum", True)
        assert a == b

    def test_field_order_does_not_change_it(self):
        assert CP.fingerprint(_fc("A", "B"), 2022, "x", True) == \
            CP.fingerprint(_fc("B", "A"), 2022, "x", True)

    def test_a_redrawn_boundary_changes_it(self):
        """Two runs over four fields whose boundaries were redrawn between them
        are different runs, and a field COUNT would not notice."""
        assert CP.fingerprint(_fc("A", "B"), 2022, "x", True) != \
            CP.fingerprint(_fc("A", "B", side=0.005), 2022, "x", True)

    def test_the_season_the_crop_and_the_series_all_change_it(self):
        base = CP.fingerprint(_fc("A"), 2022, "sorghum", True)
        assert CP.fingerprint(_fc("A"), 2023, "sorghum", True) != base
        assert CP.fingerprint(_fc("A"), 2022, "wheat", True) != base
        assert CP.fingerprint(_fc("A"), 2022, "sorghum", False) != base

    def test_a_corrected_crop_label_on_a_field_changes_it(self):
        """Somebody fixing a field's crop between attempts has changed the
        question for that field."""
        fc = _fc("A")
        fc2 = json.loads(json.dumps(fc))
        fc2["features"][0]["properties"]["crop"] = "wheat"
        assert CP.fingerprint(fc, 2022, "sorghum", True) != \
            CP.fingerprint(fc2, 2022, "sorghum", True)


class TestResuming:
    def _cp(self, tmp_path, fp="abc", enabled=True):
        return CP.Checkpoint(str(tmp_path / "out.json"), fp, enabled=enabled)

    def test_a_fresh_run_has_nothing_to_resume(self, tmp_path):
        c = self._cp(tmp_path)
        assert c.resume() == {}
        assert c.status == "FRESH"

    def test_completed_fields_come_back(self, tmp_path):
        c = self._cp(tmp_path)
        c.resume()
        c.add({"name": "A", "value": 1})
        c.add({"name": "B", "value": 2})

        c2 = self._cp(tmp_path)
        done = c2.resume()
        assert set(done) == {"A", "B"}
        assert done["A"]["value"] == 1
        assert c2.status == "RESUMED"
        assert "2 fields already analysed" in c2.note
        assert c2.note_ar

    def test_a_checkpoint_for_a_different_question_is_discarded_loudly(
            self, tmp_path):
        """Merging would produce a report that is half one question and half
        another, with nothing on its face to say so. That is worse than losing
        the run, because it is wrong rather than absent."""
        c = self._cp(tmp_path, fp="question-one")
        c.resume()
        c.add({"name": "A"})

        c2 = self._cp(tmp_path, fp="question-two")
        assert c2.resume() == {}
        assert c2.status == "STALE"
        assert "different run" in c2.note
        assert c2.note_ar

    def test_a_truncated_checkpoint_starts_over_rather_than_crashing(
            self, tmp_path):
        """A process that died mid-write is the commonest way to get one."""
        c = self._cp(tmp_path)
        c.resume()
        c.add({"name": "A"})
        with open(c.path, "w", encoding="utf-8") as fh:
            fh.write('{"fingerprint": "abc", "fields": [{"na')

        c2 = self._cp(tmp_path)
        assert c2.resume() == {}
        assert c2.status == "UNREADABLE"
        assert "starting over" in c2.note

    def test_restart_discards_and_says_so(self, tmp_path):
        c = self._cp(tmp_path)
        c.resume()
        c.add({"name": "A"})
        c2 = self._cp(tmp_path)
        assert c2.resume(restart=True) == {}
        assert c2.status == "DISCARDED"

    def test_disabling_it_writes_nothing(self, tmp_path):
        c = self._cp(tmp_path, enabled=False)
        c.resume()
        c.add({"name": "A"})
        assert not os.path.exists(c.path)


class TestTheHalfFinishedFileIsNotAReport:
    def test_the_partial_is_written_beside_the_report_not_at_it(self, tmp_path):
        """A half-finished report at the report's own path would be read AS a
        report, and a farm whose worst fields came last would look fine."""
        out = str(tmp_path / "farm_report.json")
        c = CP.Checkpoint(out, "fp")
        c.resume()
        c.add({"name": "A"})
        assert os.path.exists(out + ".partial")
        assert not os.path.exists(out)

    def test_it_is_removed_when_the_run_finishes(self, tmp_path):
        c = CP.Checkpoint(str(tmp_path / "o.json"), "fp")
        c.resume()
        c.add({"name": "A"})
        c.done()
        assert not os.path.exists(c.path)

    def test_a_crash_mid_write_leaves_the_last_good_checkpoint(self, tmp_path):
        """Written to a temporary file and moved into place."""
        c = CP.Checkpoint(str(tmp_path / "o.json"), "fp")
        c.resume()
        c.add({"name": "A"})
        good = open(c.path, encoding="utf-8").read()
        assert json.loads(good)["n_fields"] == 1
        assert not os.path.exists(c.path + ".tmp")

    def test_a_partial_can_be_inspected_without_resuming(self, tmp_path):
        """The app offers the choice rather than making it."""
        out = str(tmp_path / "o.json")
        c = CP.Checkpoint(out, "fp")
        c.resume()
        c.add({"name": "A"})
        c.add({"name": "B"})
        info = CP.find_partial(out)
        assert info["readable"] is True and info["n_fields"] == 2
        assert info["fingerprint"] == "fp"

    def test_no_partial_is_not_an_error(self, tmp_path):
        assert CP.find_partial(str(tmp_path / "nothing.json")) is None


class TestTheEngineActuallyResumes:
    def test_a_second_run_reuses_the_checkpointed_fields(self, ee_env,
                                                         tmp_path):
        """The join that matters: the engine writes what it can read back."""
        import importlib
        import agri_engine as ag
        importlib.reload(ag)

        fc = _fc("A", "B")
        out = str(tmp_path / "farm_report.json")

        # Simulate a run that died after one field by writing its checkpoint.
        fp = CP.fingerprint(fc, 2022, "sorghum", False)
        c = CP.Checkpoint(out, fp)
        c.resume()
        c.add({"name": "A", "marker": "from-the-checkpoint"})

        res = ag.analyse_farm(fc, 2022, out, crop="sorghum",
                              with_series=False)
        by = {f["name"]: f for f in res["fields"]}
        assert by["A"].get("marker") == "from-the-checkpoint"
        assert "marker" not in by["B"]
        assert res["checkpoint"]["status"] == "RESUMED"
        assert not os.path.exists(out + ".partial")

    def test_a_changed_season_does_not_resume_into_the_wrong_year(
            self, ee_env, tmp_path):
        import importlib
        import agri_engine as ag
        importlib.reload(ag)

        fc = _fc("A")
        out = str(tmp_path / "r.json")
        c = CP.Checkpoint(out, CP.fingerprint(fc, 2021, "sorghum", False))
        c.resume()
        c.add({"name": "A", "marker": "last-season"})

        res = ag.analyse_farm(fc, 2022, out, crop="sorghum",
                              with_series=False)
        assert res["checkpoint"]["status"] == "STALE"
        assert "marker" not in res["fields"][0]


class TestTheReportTellsTheTruthAboutWhatWasResumed:
    """
    `n_recovered` was len(self.records), which after a successful FRESH run is
    every field in the farm. The first live run produced

        {"status": "FRESH", ..., "n_recovered": 4}

    on a run that had resumed nothing - so a reader checking whether a run was
    clean was told the exact opposite of the truth.
    """
    def test_a_fresh_run_recovered_nothing(self, tmp_path):
        c = CP.Checkpoint(str(tmp_path / "o.json"), "fp")
        c.resume()
        for name in ("A", "B", "C", "D"):
            c.add({"name": name})
        d = c.describe()
        assert d["status"] == "FRESH"
        assert d["n_recovered"] == 0
        assert d["n_written"] == 4

    def test_a_resumed_run_counts_only_what_came_back(self, tmp_path):
        """Not what it went on to write afterwards."""
        out = str(tmp_path / "o.json")
        first = CP.Checkpoint(out, "fp")
        first.resume()
        first.add({"name": "A"})
        first.add({"name": "B"})

        second = CP.Checkpoint(out, "fp")
        assert len(second.resume()) == 2
        second.add({"name": "C"})
        second.add({"name": "D"})
        d = second.describe()
        assert d["status"] == "RESUMED"
        assert d["n_recovered"] == 2
        assert d["n_written"] == 4

    def test_a_discarded_checkpoint_recovered_nothing(self, tmp_path):
        out = str(tmp_path / "o.json")
        first = CP.Checkpoint(out, "one")
        first.resume()
        first.add({"name": "A"})
        second = CP.Checkpoint(out, "two")
        second.resume()
        second.add({"name": "A"})
        assert second.describe()["n_recovered"] == 0
