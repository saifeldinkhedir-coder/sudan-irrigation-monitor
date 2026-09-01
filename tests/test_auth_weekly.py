"""
The gate, and the weekly job.

The auth tests are mostly about the states a gate can get into that are worse
than having no gate: falling open when its configuration is corrupt, shipping a
default credential, and telling an attacker which half of their guess was
right. The weekly-job tests are about failing loudly, because a scheduled job
that fails silently is worse than no scheduled job - everybody believes the
farm is being watched, and it has not been watched since March.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "farmer_app"))
import auth as A
import weekly as W


class TestPasswordStorage:
    def test_a_password_verifies_against_its_own_record(self):
        rec = A.hash_password("correct horse battery")
        assert A.verify_password("correct horse battery", rec) is True

    def test_a_wrong_password_does_not(self):
        rec = A.hash_password("correct horse battery")
        assert A.verify_password("correct horse batteru", rec) is False

    def test_the_password_is_not_in_the_record(self):
        rec = A.hash_password("hunter2")
        assert "hunter2" not in json.dumps(rec)

    def test_two_users_with_the_same_password_get_different_hashes(self):
        """A shared salt would let one stolen file reveal which accounts share
        a password."""
        a, b = A.hash_password("same"), A.hash_password("same")
        assert a["salt"] != b["salt"] and a["hash"] != b["hash"]

    def test_the_iteration_count_is_not_token(self):
        assert A.hash_password("x")["iterations"] >= 100_000

    def test_a_malformed_record_fails_closed(self):
        assert A.verify_password("x", {}) is False
        assert A.verify_password("x", {"salt": "zz", "hash": "yy"}) is False


class TestTheUsersFile:
    def test_no_users_file_means_open_and_is_distinguishable(self, tmp_path):
        """"Open" and "nobody can get in" are different answers, and the caller
        has to be able to tell them apart."""
        assert A.load_users(str(tmp_path / "none.json")) is None
        p = str(tmp_path / "empty.json")
        A.save_users({"users": {}}, p)
        assert A.load_users(p) == {"users": {}}

    def test_there_is_no_default_credential(self, tmp_path):
        """A default credential is a credential everybody has. Checked by
        behaviour rather than by grepping prose: the module explains at length
        why it does NOT fall back to admin/admin, and a text search finds that
        explanation."""
        p = str(tmp_path / "fresh.json")
        A.save_users({"users": {}}, p)
        for name in ("admin", "root", "user", "officer"):
            for pw in ("admin", "password", "1234", ""):
                assert A.authenticate(name, pw, p) is None

    def test_a_corrupt_users_file_does_not_fall_open(self, tmp_path):
        """A gate that opens when its configuration is corrupt is worse than no
        gate, because nobody is watching for it."""
        p = str(tmp_path / "users.json")
        open(p, "w", encoding="utf-8").write("{not json")
        users = A.load_users(p)
        assert users is not None            # not "open"
        assert users["users"] == {}         # and nobody gets in
        assert users["error"]

    def test_a_user_can_sign_in(self, tmp_path):
        p = str(tmp_path / "u.json")
        A.add_user("ali", "a-long-enough-password", path=p)
        assert A.authenticate("ali", "a-long-enough-password", p)["name"] == "ali"

    def test_a_wrong_password_is_refused(self, tmp_path):
        p = str(tmp_path / "u.json")
        A.add_user("ali", "a-long-enough-password", path=p)
        assert A.authenticate("ali", "wrong", p) is None

    def test_an_unknown_user_is_refused_the_same_way(self, tmp_path):
        """Telling somebody the name was right is telling them half the
        answer."""
        p = str(tmp_path / "u.json")
        A.add_user("ali", "a-long-enough-password", path=p)
        assert A.authenticate("nobody", "a-long-enough-password", p) is None

    def test_authentication_against_an_open_deployment_returns_none(self,
                                                                    tmp_path):
        assert A.authenticate("x", "y", str(tmp_path / "none.json")) is None


class TestTheWarningIsPrintedOnceNotOnEveryRerun:
    """
    Streamlit re-executes the whole script on every interaction, so a bare
    print() in the gate repeated the open-deployment warning on every click
    until it filled the terminal - nine copies before the user had even
    clicked anything.

    Moving noise out of the sidebar and into the log is not a fix if it is
    still noise. A warning that appears every single time is a warning nobody
    reads, including the ones that matter.
    """
    def _run(self, times, tmp_path):
        import contextlib
        import io as _io
        A._WARNED = False
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            for _ in range(times):
                A.gate(path=str(tmp_path / "none.json"), quiet=True)
        return buf.getvalue()

    def test_it_is_said_once_however_many_reruns(self, tmp_path):
        out = self._run(9, tmp_path)
        assert out.count("SECURITY") == 1

    def test_it_is_still_said_at_all(self, tmp_path):
        """Quieting a warning into silence would be the other failure."""
        out = self._run(1, tmp_path)
        assert "OPEN" in out

    def test_the_command_it_recommends_actually_runs(self):
        """A warning that ends in a command nobody can run is half a warning."""
        import subprocess
        import sys
        assert "python -m farmer_app.auth add" in A.OPEN_WARNING
        root = os.path.join(os.path.dirname(__file__), "..")
        r = subprocess.run([sys.executable, "-m", "farmer_app.auth", "list"],
                           cwd=root, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-400:]


class TestScoping:
    def test_a_user_with_no_list_sees_every_farm(self):
        assert A.may_see({"name": "a", "farms": None}, "any farm") is True

    def test_a_scoped_user_sees_only_their_own(self):
        u = {"name": "a", "farms": ["block 14"]}
        assert A.may_see(u, "block 14") is True
        assert A.may_see(u, "block 15") is False

    def test_an_open_deployment_shows_everything(self):
        """None means open, not denied. Conflating them makes the gate either
        useless or unopenable."""
        assert A.may_see(None, "anything") is True


class TestTheGateStatesItsLimits:
    def test_it_says_it_is_not_transport_security(self):
        """A security control that overstates itself is worse than none,
        because people stop taking the other precautions."""
        assert "HTTPS" in A.NOT_ENOUGH
        assert A.NOT_ENOUGH_AR

    def test_an_open_deployment_warns_in_both_languages(self):
        assert "OPEN" in A.OPEN_WARNING
        assert "مفتوح" in A.OPEN_WARNING_AR

    def test_the_module_docstring_says_what_it_is_not(self):
        assert "not an identity system" in A.__doc__.lower()


# ==============================================================================
# THE WEEKLY JOB
# ==============================================================================

class TestTheWeeklyJob:
    def _fields(self, tmp_path, n=1):
        p = tmp_path / "f.geojson"
        feats = [{"type": "Feature", "properties": {"name": f"F{i}"},
                  "geometry": {"type": "Polygon", "coordinates": [[
                      [33.1, 14.4], [33.104, 14.4], [33.104, 14.404],
                      [33.1, 14.404], [33.1, 14.4]]]}} for i in range(n)]
        p.write_text(json.dumps({"type": "FeatureCollection",
                                 "features": feats}), encoding="utf-8")
        return str(p)

    def test_a_missing_field_file_fails_rather_than_running(self, tmp_path):
        out = W.run_once("F", str(tmp_path / "nope.geojson"), 2022)
        assert out["ok"] is False
        assert out["steps"][0]["step"] == "fields"

    def test_a_failed_run_is_reported_not_swallowed(self, tmp_path,
                                                    monkeypatch):
        """The exception text is the most useful thing a failed scheduled run
        produces."""
        def boom(*a, **k):
            raise RuntimeError("quota exceeded")
        monkeypatch.setattr(W.agri_engine, "analyse_farm", boom)
        out = W.run_once("F", self._fields(tmp_path), 2022)
        assert out["ok"] is False
        assert "quota exceeded" in W.digest(out)
        assert "THIS RUN FAILED" in W.digest(out)

    def test_a_full_cycle_runs_records_compares_and_exports(self, ee_env,
                                                            tmp_path):
        import importlib
        importlib.reload(W.agri_engine)
        fields = self._fields(tmp_path, n=2)
        out_json = str(tmp_path / "farm_report.json")
        runs_root = str(tmp_path / "runs")

        first = W.run_once("Block 14", fields, 2022, out_json=out_json,
                           runs_root=runs_root, observations_db="")
        assert first["ok"] is True
        assert os.path.exists(str(tmp_path / "farm_report.html"))
        steps = {s["step"]: s for s in first["steps"]}
        assert steps["engine"]["ok"] and steps["recorded"]["ok"]
        # One run is not a comparison, and it says so rather than reporting
        # zero change.
        assert "nothing to compare" in steps["compared"]["detail"] \
            or "only one run" in steps["compared"]["detail"]

        second = W.run_once("Block 14", fields, 2022, out_json=out_json,
                            runs_root=runs_root, observations_db="")
        assert second["ok"] is True
        assert "change" in second
        assert second["change"]["headline"]

    def test_the_history_accumulates(self, ee_env, tmp_path):
        import importlib
        import runs as RUNS
        importlib.reload(W.agri_engine)
        fields = self._fields(tmp_path)
        runs_root = str(tmp_path / "runs")
        for _ in range(2):
            W.run_once("F", fields, 2022, out_json=str(tmp_path / "r.json"),
                       runs_root=runs_root, observations_db="")
        assert len(RUNS.RunStore(runs_root).runs("F")) >= 1


class TestItWillNotScheduleItself:
    def test_it_prints_the_line_rather_than_installing_it(self):
        """A tool that quietly arranges to run itself every week on a laptop it
        does not own has made a decision that was not its to make."""
        hint = W.schedule_hint("Block 14", "b14.geojson", 2022)
        assert hint["line"] and hint["command"] in hint["line"]
        assert hint["note"]

    def test_the_module_never_calls_a_scheduler(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                                "weekly.py"), encoding="utf-8").read()
        for call in ("subprocess.run", "subprocess.Popen", "os.system"):
            assert call not in src

    def test_the_digest_is_readable_in_a_mail_notification(self):
        out = {"farm": "Block 14", "started_utc": "2026-09-01T06:00:00+00:00",
               "ok": True,
               "steps": [{"step": "engine", "ok": True, "detail": "40 fields"}],
               "change": {"headline": "Of 40 fields: 2 declined before peak",
                          "headline_ar": "من 40 حقلًا: 2 تراجعت",
                          "crossings": [{"name": "F7", "from": "ok",
                                         "to": "attention"}]}}
        text = W.digest(out)
        assert "Block 14" in text and "40 fields" in text
        assert "F7: ok -> attention" in text
        assert "من 40" in W.digest(out, ar=True)
