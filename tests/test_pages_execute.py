"""
The pages, actually executed.

Coverage said 773 statements had never been run by anything, and the worst of
them was farmer_app/record.py at zero - the data-entry page, which is the only
route by which any gate in this platform is ever unlocked.

Two shipped defects would have died here in a second: `_render_map` called and
never defined, and a whole sidebar page rendering as `page_units` because a
missing label falls back to its own key. Both are invisible to a test that only
imports a module, because a name used inside a function body is not resolved
until that body runs.

These tests assert that a page RUNS and what it tried to say. They assert
nothing about how it looks.
"""

import json
import os
import sys

import pytest

# console/ is NOT on the path. It contains app.py too, and putting it here
# made `import app` resolve to the console instead of the farm screen - the
# farm-screen tests were quietly exercising the wrong application. The console
# is loaded by file path below.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "farmer_app"))

import stub_streamlit as SS

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEMO_REPORT = os.path.join(ROOT, "docs", "farm_report_demo.json")
DEMO_FIELDS = os.path.join(ROOT, "docs", "gezira_fields_demo.geojson")


@pytest.fixture
def report():
    with open(DEMO_REPORT, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def fields():
    with open(DEMO_FIELDS, encoding="utf-8") as fh:
        return json.load(fh)


# ==============================================================================
# THE STUB ITSELF
# ==============================================================================

class TestTheStubIsWorthTrusting:
    def test_it_records_what_a_page_renders(self):
        def page():
            import streamlit as st
            st.markdown("hello")
            st.caption("world")
        rec = SS.run(page)
        assert rec.said("hello") and rec.said("world")

    def test_st_stop_ends_the_page_rather_than_failing_the_test(self):
        def page():
            import streamlit as st
            st.markdown("before")
            st.stop()
            st.markdown("after")
        rec = SS.run(page)
        assert rec.said("before") and not rec.said("after")
        assert rec.stopped is True

    def test_a_missing_attribute_would_still_raise(self):
        """A stub that swallows everything tests nothing."""
        def page():
            import streamlit as st
            st.markdown(undefined_name)          # noqa: F821
        with pytest.raises(NameError):
            SS.run(page)

    def test_the_real_streamlit_comes_back_afterwards(self):
        import streamlit as before
        SS.run(lambda: None)
        import streamlit as after
        assert before is after


# ==============================================================================
# THE FARM SCREEN
# ==============================================================================

class TestTheFarmScreenRuns:
    def _run(self, monkeypatch, state=None):
        import app as APP
        monkeypatch.setattr(sys, "argv",
                            ["app.py", "--report", DEMO_REPORT,
                             "--fields", DEMO_FIELDS])
        # The map component talks to a browser; the page's own logic is what
        # is under test, so it returns the shape st_folium returns.
        import fieldmap as FM
        monkeypatch.setattr(FM, "render", lambda *a, **k: {})
        return SS.run(APP.main, state=state or {})

    def test_it_runs_top_to_bottom(self, monkeypatch):
        rec = self._run(monkeypatch)
        assert rec.calls, "the page rendered nothing at all"
        assert not rec.errors, rec.errors

    def test_it_names_the_farm_and_its_fields(self, monkeypatch):
        rec = self._run(monkeypatch)
        assert rec.said("مراقب المزرعة")
        assert any("Field" in t for t in rec.text)

    def test_the_demonstration_caveat_is_on_the_page(self, monkeypatch):
        """Real imagery over invented boundaries has to say so."""
        rec = self._run(monkeypatch)
        assert rec.said("عرض توضيحي")

    def test_no_label_falls_back_to_its_own_key(self, monkeypatch):
        """A whole sidebar page once rendered as `page_units`. A key that
        reaches the screen is a label nobody wrote."""
        import ui
        rec = self._run(monkeypatch)
        leaked = sorted({k for k in ui.T
                         if any(k == t.strip() for t in rec.text)})
        assert not leaked, f"untranslated keys on screen: {leaked}"

    def test_it_runs_in_english_too(self, monkeypatch):
        import app as APP
        import fieldmap as FM
        monkeypatch.setattr(sys, "argv",
                            ["app.py", "--report", DEMO_REPORT,
                             "--fields", DEMO_FIELDS])
        monkeypatch.setattr(FM, "render", lambda *a, **k: {})
        with SS.installed({}) as rec:
            import streamlit as stub
            stub.sidebar.radio = lambda *a, **k: "English"
            try:
                APP.main()
            except Exception as e:                      # noqa: BLE001
                if type(e).__name__ != "_Stop":
                    raise
        assert not rec.errors, rec.errors
        assert rec.said("Farm Monitor")

    def test_a_report_that_does_not_exist_does_not_crash(self, monkeypatch):
        import app as APP
        import fieldmap as FM
        monkeypatch.setattr(sys, "argv",
                            ["app.py", "--report", "no_such_report.json",
                             "--fields", DEMO_FIELDS])
        monkeypatch.setattr(FM, "render", lambda *a, **k: {})
        rec = SS.run(APP.main, state={})
        assert rec.said("console/app.py"), "it must say where a run happens"


# ==============================================================================
# THE DATA-ENTRY PAGE - the only route that unlocks anything
# ==============================================================================

class TestTheRecordPageRuns:
    def _run(self, report, tmp_path, tab=0):
        import record as R
        # Every tab is rendered in one pass; the stub's tabs() hands back
        # containers rather than selecting one.
        return SS.run(R.render, report, str(tmp_path), state={})

    def test_it_runs_at_all(self, report, tmp_path):
        """196 statements, zero coverage, and it is the only way any gate in
        this platform is ever unlocked."""
        rec = self._run(report, tmp_path)
        assert rec.calls
        assert not rec.errors, rec.errors

    def test_it_creates_its_stores_rather_than_failing_without_them(
            self, report, tmp_path):
        self._run(report, tmp_path)
        made = sorted(p.name for p in tmp_path.glob("*.db"))
        assert made, "the page created no store"

    def test_it_says_everything_here_is_reported_not_measured(self, report,
                                                              tmp_path):
        rec = self._run(report, tmp_path)
        assert any("REPORTED" in t for t in rec.text)

    def test_it_offers_the_fields_from_the_report(self, report, tmp_path):
        rec = self._run(report, tmp_path)
        names = [f["name"] for f in report["fields"]]
        offered = [k.get("options", a[1] if len(a) > 1 else None)
                   for a, k in rec.of("selectbox")]
        flat = [x for o in offered if o for x in o]
        assert any(n in flat for n in names)

    def test_an_empty_report_does_not_crash_it(self, tmp_path):
        rec = self._run({"fields": []}, tmp_path)
        assert not rec.errors, rec.errors


# ==============================================================================
# THE CONSOLE
# ==============================================================================

class TestTheConsoleRuns:
    def _run(self, monkeypatch, page, tmp_path):
        """Load the console by file path, with the stub already installed.

        The module is executed AFTER the swap, so its own
        `import streamlit as st` picks the stub up - a module built with
        importlib is not registered in sys.modules, so the repointing pass
        cannot reach it afterwards.
        """
        import importlib.util
        monkeypatch.setattr(sys, "argv",
                            ["app.py", "--report", DEMO_REPORT,
                             "--fields", DEMO_FIELDS, "--farm", "demo"])
        monkeypatch.chdir(tmp_path)

        stub, rec = SS.make({})
        stub.sidebar.radio = lambda *a, **k: (
            "العربية" if "اللغة" in str(a[0]) else page)
        stub.sidebar.text_input = lambda *a, **k: (
            DEMO_REPORT if "report" in str(a[0]).lower() else
            DEMO_FIELDS if "polygon" in str(a[0]).lower() else "demo")

        saved = sys.modules.get("streamlit")
        sys.modules["streamlit"] = stub
        touched = [m for m in list(sys.modules.values())
                   if m is not None and getattr(m, "st", None) is saved]
        for m in touched:
            m.st = stub
        try:
            spec = importlib.util.spec_from_file_location(
                "console_app", os.path.join(ROOT, "console", "app.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.main()
        except Exception as e:                          # noqa: BLE001
            if type(e).__name__ != "_Stop":
                raise
        finally:
            sys.modules["streamlit"] = saved
            for m in touched:
                m.st = saved
        return rec

    @pytest.mark.parametrize("page", ["run", "changes", "record", "units",
                                      "backup", "about"])
    def test_every_console_page_runs(self, monkeypatch, tmp_path, page):
        """137 statements at zero coverage, holding every operator function."""
        rec = self._run(monkeypatch, page, tmp_path)
        assert rec.calls, f"the {page} page rendered nothing"
        assert not rec.errors, f"{page}: {rec.errors}"

    def test_the_console_names_itself_and_points_at_the_farm_screen(
            self, monkeypatch, tmp_path):
        rec = self._run(monkeypatch, "about", tmp_path)
        assert rec.said("مشغِّل مراقب المزرعة")


# ==============================================================================
# THE CHANGE PAGE
# ==============================================================================

class TestTheChangePageRuns:
    def test_with_no_history_it_says_so_and_stops(self, report, tmp_path):
        import changes as CG
        rec = SS.run(CG.render, report, True, "nobody", str(tmp_path))
        assert rec.said("لا شيء للمقارنة بعد.")

    def test_with_two_runs_it_compares_them(self, report, tmp_path):
        import runs as RUNS
        store = RUNS.RunStore(str(tmp_path))
        p = tmp_path / "r.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        store.record("demo", str(p))
        # A second run with one field moved, so there is something to report.
        moved = json.loads(json.dumps(report))
        v = moved["fields"][0]["crop_health"]["readings"]["vigour"]
        v["value"] = round((v["value"] or 0.3) - 0.25, 4)
        p2 = tmp_path / "r2.json"
        p2.write_text(json.dumps(moved), encoding="utf-8")
        store.record("demo", str(p2))

        import changes as CG
        rec = SS.run(CG.render, moved, True, "demo", str(tmp_path))
        assert rec.calls and not rec.errors
        assert any("NDVI" in t for t in rec.text)


# ==============================================================================
# ONBOARDING AND ABOUT
# ==============================================================================

class TestTheOtherPagesRun:
    def test_the_first_screen_offers_three_ways_in(self):
        import onboarding as ONB
        rec = SS.run(ONB.render, True)
        assert rec.said("ارسم حقولي على الخريطة")
        assert rec.said("عندي ملف حقول")
        assert rec.said("أرني العرض التوضيحي")

    def test_the_demo_option_carries_its_caveat_beside_it(self):
        """Not on a page the reader has to find."""
        import onboarding as ONB
        rec = SS.run(ONB.render, True)
        assert rec.said("لا تخصّ مزرعة أحد")

    def test_the_about_page_runs_in_both_languages(self, report):
        import about as A
        for ar in (True, False):
            rec = SS.run(A.render, report, ar)
            assert rec.calls and not rec.errors

    def test_the_about_page_carries_the_disease_refusal(self, report):
        import about as A
        rec = SS.run(A.render, report, True)
        assert any("لا تسمّي هذه الأداة مرضًا" in t for t in rec.text)


# ==============================================================================
# THE SCHEME-MANAGER VIEWS
# ==============================================================================

class TestTheNetworkDashboardsRun:
    """
    308 statements at zero coverage across two pages. `dashboard/map_app.py`
    was worse than untested: nothing launched it, and an identical copy of it
    sat at the repository root. A feature nobody can start is not a feature,
    and two copies of one is a maintenance cost with no reader.
    """
    def _load(self, name, argv, tmp_path, monkeypatch):
        import importlib.util
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.chdir(ROOT)
        stub, rec = SS.make({})
        saved = sys.modules.get("streamlit")
        sys.modules["streamlit"] = stub
        touched = [m for m in list(sys.modules.values())
                   if m is not None and getattr(m, "st", None) is saved]
        for m in touched:
            m.st = stub
        # dashboard/ has its own `data` module; keep it off the shared name.
        sys.path.insert(0, os.path.join(ROOT, "dashboard"))
        try:
            spec = importlib.util.spec_from_file_location(
                f"dash_{name}", os.path.join(ROOT, "dashboard", name))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.main()
        except Exception as e:                          # noqa: BLE001
            if type(e).__name__ != "_Stop":
                raise
        finally:
            sys.path.remove(os.path.join(ROOT, "dashboard"))
            sys.modules["streamlit"] = saved
            for m in touched:
                m.st = saved
            for k in [k for k in sys.modules if k.startswith("dash_")]:
                sys.modules.pop(k, None)
        return rec

    def test_the_table_dashboard_runs(self, tmp_path, monkeypatch):
        rec = self._load("app.py",
                         ["app.py", "--results", "docs/sample_results.json"],
                         tmp_path, monkeypatch)
        assert rec.calls
        # st.error here is a deliberate REFUSAL, not a crash. A page that
        # declines to report an area is doing its job; a page that raises is
        # not. The two look identical to a naive assertion, so this checks the
        # text rather than the count.
        crashes = [e for e in rec.errors
                   if "Error" in e or "Traceback" in e or "object at 0x" in e]
        assert not crashes, crashes

    def test_the_dashboard_refuses_an_area_named_with_a_claim(self, tmp_path,
                                                              monkeypatch):
        """The neutrality check, exercised end to end for the first time. A
        name carrying a land claim is a thing this tool will not put a figure
        against, because the figure would then be evidence in the claim."""
        rec = self._load("app.py",
                         ["app.py", "--results", "docs/sample_results.json"],
                         tmp_path, monkeypatch)
        assert any("claim language" in e for e in rec.errors)
        assert any("neutral identifier" in e for e in rec.errors)

    def test_the_map_dashboard_runs(self, tmp_path, monkeypatch):
        rec = self._load(
            "map_app.py",
            ["map_app.py", "--results", "docs/sample_results.json",
             "--canals", "dashboard/newhalfa_canals.geojson",
             "--command", "dashboard/newhalfa_command.geojson"],
            tmp_path, monkeypatch)
        assert rec.calls and not rec.errors, rec.errors

    def test_the_map_view_is_launchable(self):
        """It was reachable only by typing its path from memory."""
        with open(os.path.join(ROOT, ".claude", "launch.json"),
                  encoding="utf-8") as fh:
            cfg = json.load(fh)
        args = " ".join(a for c in cfg["configurations"]
                        for a in c.get("runtimeArgs", []))
        assert "dashboard/map_app.py" in args

    def test_there_is_only_one_copy_of_it(self):
        """An identical duplicate sat at the repository root."""
        assert not os.path.exists(os.path.join(ROOT, "map_app.py"))


# ==============================================================================
# THE COMMAND LINE ENTRY POINTS
# ==============================================================================

class TestTheCommandLinesRun:
    """Thin wrappers, and the only way the engines are started in production.
    A broken argument here is a run that never happens."""

    def _cli(self, script, args):
        import subprocess
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "src", script)] + args,
            capture_output=True, text=True, cwd=ROOT, timeout=120)

    def test_the_farm_cli_explains_itself(self):
        out = self._cli("farm_cli.py", ["--help"])
        assert out.returncode == 0
        for flag in ("--fields", "--season", "--crop", "--out",
                     "--observations", "--restart"):
            assert flag in out.stdout, flag

    def test_the_network_cli_explains_itself(self):
        out = self._cli("cli.py", ["--help"])
        assert out.returncode == 0
        assert "--season" in out.stdout

    def test_a_missing_field_file_is_refused_before_any_earth_engine_work(self):
        out = self._cli("farm_cli.py",
                        ["--fields", "no_such_file.geojson", "--out", "x.json"])
        assert out.returncode != 0
        assert "not found" in (out.stdout + out.stderr).lower()

    def test_an_empty_field_file_is_refused_with_the_reason(self, tmp_path):
        """"There is no honest way to invent a field boundary."" """
        p = tmp_path / "empty.geojson"
        p.write_text('{"type":"FeatureCollection","features":[]}',
                     encoding="utf-8")
        out = self._cli("farm_cli.py",
                        ["--fields", str(p), "--out", str(tmp_path / "o.json")])
        assert out.returncode != 0
        assert "invent" in (out.stdout + out.stderr)

    def test_the_weekly_job_prints_a_schedule_line_rather_than_installing_one(
            self):
        import subprocess
        out = subprocess.run(
            [sys.executable, os.path.join(ROOT, "src", "weekly.py"),
             "--farm", "demo", "--fields", "f.geojson", "--season", "2022",
             "--show-schedule"],
            capture_output=True, text=True, cwd=ROOT, timeout=60)
        assert out.returncode == 0
        assert "weekly.py" in out.stdout
