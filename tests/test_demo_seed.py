"""
The demonstration data set, and the safeguards that keep it from lying.

Half of what this platform does is refuse. A demonstration in which every gate
is shut shows only half the design: a reader sees "not available" nine times
and cannot tell whether the tool is careful or unfinished. What makes it
careful is what happens WHEN a gate opens - the figure arrives with its error,
its sample count, and who measured it.

So the seeder exists, and every test here is about the line between showing the
unlocked state and inventing a measurement.
"""

import os
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import seed_demo as SD                                      # noqa: E402


@pytest.fixture
def seeded(tmp_path):
    return SD.seed(str(tmp_path)), str(tmp_path)


class TestItOpensTheGates:
    def test_the_yield_gate_opens_and_quotes_its_error(self, seeded):
        """The point of the demonstration: not a number, a number WITH its
        error and its sample count."""
        import agronomy as ag
        _out, path = seeded
        store = ag.YieldCalibrationStore(
            os.path.join(path, "yield_calibration.db"))
        model = store.model("sorghum")
        est = ag.yield_estimate(0.42, "sorghum", model)
        assert est["claim_level"] == "calibrated"
        assert est["yield_t_ha"] is not None
        assert model["n_points"] == 30
        assert model["rmse_fraction"] is not None

    def test_it_recovers_the_line_it_was_drawn_from(self, seeded):
        """The fit is meant to be good. That is a demonstration of a working
        calibration, not a finding about sorghum - and the declared line is
        printed on creation so nobody can mistake the two."""
        out, _path = seeded
        fit = out["yield_fit"]
        assert fit["fitted"] is True
        assert abs(fit["slope"] - SD.YIELD_SLOPE) < 1.0
        assert fit["r2"] > 0.9

    def test_the_disease_ladder_reaches_its_top_rung(self, seeded):
        """REPORTED is the only rung that names a disease, and until now
        nothing had ever stood on it."""
        import disease as dz
        import nutrition_climate_ground as ncg
        _out, path = seeded
        store = ncg.ObservationStore(os.path.join(path, "observations.db"))
        try:
            found = store.scouting_for("Field 2")
            assert [r["problem"] for r in found] == ["sorghum_anthracnose"]
            assert dz.diagnose(scouting=found)["claim_level"] == "REPORTED"
        finally:
            store.close()

    def test_a_clean_walk_names_nothing(self, seeded):
        """Two of the four records are clean walks. They matter as much: they
        are what makes the agreement rate a rate rather than a count of
        problems."""
        import nutrition_climate_ground as ncg
        _out, path = seeded
        store = ncg.ObservationStore(os.path.join(path, "observations.db"))
        try:
            assert store.scouting_for("Field 3") == []
        finally:
            store.close()

    def test_the_agreement_rate_can_finally_exist(self, seeded):
        """The only figure that MEASURES this platform's accuracy rather than
        claiming it. It has been structurally at zero since the beginning."""
        import nutrition_climate_ground as ncg
        _out, path = seeded
        store = ncg.ObservationStore(os.path.join(path, "observations.db"))
        try:
            report = {"fields": [
                {"name": f"Field {i}",
                 "crop_health": {"readings": {"vigour": {
                     "status": "OK", "value": v}}}}
                for i, v in enumerate([0.30, 0.22, 0.39, 0.42], start=1)]}
            for row in store.conn.execute(
                    "SELECT obs_id, field_id, canopy_condition "
                    "FROM observations").fetchall():
                obs = ncg.GroundObservation(
                    obs_id=row[0], field_id=row[1], observed_at="2022-09-12",
                    lat=14.4, lon=33.1, photo_path="", canopy_condition=row[2])
                scored = ncg.score_observation(obs, report)
                obs.satellite_agreement = scored["verdict"]
                store.add(obs, satellite=scored["satellite"])
            s = store.agreement_summary()
            assert s["available"] is True
            assert 0.0 <= s["agreement_rate"] <= 1.0
        finally:
            store.close()

    def test_the_rate_is_scored_at_seed_time_not_left_to_the_reader(
            self, seeded):
        """The demo used to write observations with no satellite side, so the
        one figure worth showing was the one that did not appear."""
        out, _path = seeded
        assert out["agreement"]["available"] is True
        assert out["scored"] > 0

    def test_the_rate_is_not_a_perfect_score(self, seeded):
        """A demonstration in which the satellite agrees every time would be
        advertising, not a measurement. The synthetic walks are chosen so one
        of them disagrees."""
        out, _path = seeded
        assert out["agreement"]["agreement_rate"] < 1.0

    def test_costs_come_out_per_field(self, seeded):
        import farm_records as fr
        _out, path = seeded
        store = fr.RecordStore(os.path.join(path, "farm_records.db"))
        try:
            b = store.cost_breakdown("Field 1")
            assert b["n_operations"] == 5
            assert b["total_cost"] > 0
        finally:
            store.close()


class TestItCannotBecomeALie:
    def test_every_row_carries_the_stamp(self, seeded):
        """The stamp is in the DATA, so it survives being copied out of the
        directory that explains it."""
        _out, path = seeded
        conn = sqlite3.connect(os.path.join(path, "observations.db"))
        obs = conn.execute(
            "SELECT observer, notes FROM observations").fetchall()
        conn.close()
        assert obs and all(o[0] == SD.STAMP for o in obs)
        assert all("SYNTHETIC" in (o[1] or "") for o in obs)

        conn = sqlite3.connect(os.path.join(path, "farm_records.db"))
        ops = conn.execute("SELECT reported_by FROM operations").fetchall()
        conn.close()
        assert ops and all(o[0] == SD.STAMP for o in ops)

    def test_the_calibration_points_are_named_as_demonstration(self, seeded):
        _out, path = seeded
        conn = sqlite3.connect(os.path.join(path, "yield_calibration.db"))
        ids = conn.execute("SELECT field_id FROM yield_points").fetchall()
        conn.close()
        assert ids and all(SD.STAMP in i[0] for i in ids)

    def test_it_refuses_a_directory_holding_records_it_did_not_write(
            self, tmp_path):
        """A tool that can overwrite a season of somebody's scouting with
        invented rows is a tool that eventually will."""
        import nutrition_climate_ground as ncg
        store = ncg.ObservationStore(str(tmp_path / "observations.db"))
        try:
            store.add(ncg.GroundObservation(
                obs_id="real", field_id="F", observed_at="2022-09-01",
                lat=14.4, lon=33.1, photo_path="", observer="Ali"))
        finally:
            store.close()
        out = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "seed_demo.py"),
             "--path", str(tmp_path)],
            capture_output=True, text=True, cwd=ROOT, timeout=180)
        assert out.returncode != 0
        assert "REFUSING" in out.stdout

    def test_it_writes_nowhere_near_the_real_stores_by_default(self):
        src = open(os.path.join(ROOT, "tools", "seed_demo.py"),
                   encoding="utf-8").read()
        assert 'default="demo"' in src

    def test_the_demo_directory_is_not_committed(self):
        out = subprocess.run(["git", "check-ignore", "-q", "demo/"],
                             cwd=ROOT, capture_output=True)
        assert out.returncode == 0, "demo/ must not be committable"

    def test_the_console_announces_a_synthetic_store(self, seeded):
        """An unlocked yield in a screenshot must not be indistinguishable
        from a calibrated one."""
        import importlib.util
        _out, path = seeded
        spec = importlib.util.spec_from_file_location(
            "console_check", os.path.join(ROOT, "console", "app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod._is_synthetic(path) is True
        assert mod._is_synthetic(str(ROOT)) is False

    def test_it_invents_no_satellite_measurement(self, seeded):
        """The satellite side is real, from Earth Engine. The seeder READS the
        report - that is how a scouting record gets scored against what the
        satellite saw - and writes nothing into it.

        Checked by behaviour rather than by grepping for the word: the report
        on disk must be byte-identical after seeding."""
        import hashlib
        report = os.path.join(ROOT, "docs", "farm_report_demo.json")
        before = hashlib.sha256(open(report, "rb").read()).hexdigest()
        SD.seed(seeded[1])
        after = hashlib.sha256(open(report, "rb").read()).hexdigest()
        assert before == after, "the seeder modified the satellite report"

    def test_the_stores_it_writes_hold_no_satellite_reading_it_made_up(
            self, seeded):
        """satellite_ndvi comes from the report, or it is null. It is never a
        number this script chose."""
        import json
        _out, path = seeded
        with open(os.path.join(ROOT, "docs", "farm_report_demo.json"),
                  encoding="utf-8") as fh:
            real = {f["name"]: (f.get("crop_health") or {})
                    .get("readings", {}).get("vigour", {}).get("value")
                    for f in json.load(fh)["fields"]}
        conn = sqlite3.connect(os.path.join(path, "observations.db"))
        rows = conn.execute(
            "SELECT field_id, satellite_ndvi FROM observations").fetchall()
        conn.close()
        for field_id, ndvi in rows:
            if ndvi is not None:
                assert ndvi == real.get(field_id),                     f"{field_id}: {ndvi} is not what the satellite reported"
