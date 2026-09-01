"""
The weekly job: run, record, compare, export.

    python src/weekly.py --farm "Gezira block 14" \
        --fields blocks/14.geojson --season 2022

WHY THIS FILE EXISTS AND NOT A SCHEDULER
----------------------------------------
A monitor that needs somebody to press a button is not a monitor. But this
program will NOT install a scheduled task on anybody's machine: a tool that
quietly arranges to run itself every week on a laptop it does not own has made
a decision that was not its to make, and the person who inherits that laptop
will find a job they cannot explain.

So the job is a script, complete and idempotent, and scheduling it is one
documented line the operator runs themselves. `schedule_hint()` prints the
exact line for their platform. The decision stays with them; only the work is
automated.

WHAT IT DOES, IN ORDER
----------------------
    1. run the engine (resuming an interrupted attempt if the question is
       unchanged)
    2. record the report in the run store, so a history accumulates
    3. compare it with the previous run for the same farm
    4. write the self-contained HTML export
    5. print a digest, and exit non-zero if anything failed

Step 5 matters more than it looks. A scheduled job that fails silently is worse
than no scheduled job: everybody believes the farm is being watched, and it has
not been watched since March. The exit code is what a scheduler can see, so it
is set honestly.

WHAT IT WILL NOT DO
-------------------
It will not send anything anywhere. Delivering a report to a farmer means an
account, credentials and somebody's phone number, and those are decisions for
the operator. The digest goes to standard output, where a scheduler can mail it
using the operator's own configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime, timezone

import agri_engine
import change as CH
import report_html as RH
import runs as RUNS


def schedule_hint(farm: str, fields: str, season: int,
                  when: str = "Sunday 06:00") -> dict:
    """The exact line to schedule this, for the operator to run themselves."""
    py = sys.executable
    here = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    cmd = (f'"{py}" "{os.path.join(here, "src", "weekly.py")}" '
           f'--farm "{farm}" --fields "{fields}" --season {season}')
    if platform.system() == "Windows":
        line = (f'schtasks /Create /SC WEEKLY /D SUN /ST 06:00 '
                f'/TN "FarmMonitor-{farm}" /TR {cmd}')
        note = ("Windows Task Scheduler. The task runs as you, so it needs you "
                "logged in or the task set to run whether or not you are.")
    else:
        line = f'0 6 * * 0 cd "{here}" && {cmd} >> weekly.log 2>&1'
        note = "A crontab line. Run `crontab -e` and paste it."
    return {"platform": platform.system(), "when": when, "command": cmd,
            "line": line, "note": note}


def run_once(farm: str, fields_path: str, season: int, crop: str = "default",
             out_json: str = "farm_report.json", runs_root: str = "runs",
             observations_db: str = "observations.db",
             export_html: bool = True, ar: bool = True) -> dict:
    """One weekly cycle. Returns a digest; raises nothing it can report."""
    started = datetime.now(timezone.utc).isoformat()
    result = {"farm": farm, "started_utc": started, "steps": [], "ok": True}

    def step(name, ok, detail=""):
        result["steps"].append({"step": name, "ok": ok, "detail": detail})
        if not ok:
            result["ok"] = False

    if not os.path.exists(fields_path):
        step("fields", False, f"{fields_path} not found")
        return result
    with open(fields_path, encoding="utf-8") as fh:
        field_fc = json.load(fh)

    # 1. the run
    try:
        report = agri_engine.analyse_farm(
            field_fc, season, out_json, crop=crop, with_series=True,
            observations_db=observations_db)
        step("engine", True, f'{report.get("n_fields", 0)} fields')
    except Exception as e:                       # noqa: BLE001
        # Deliberately broad, and deliberately NOT swallowed: the exception
        # text is the most useful thing a failed scheduled run produces, and it
        # is put where a scheduler will mail it.
        step("engine", False, f"{type(e).__name__}: {e}")
        return result

    # 2. the history
    store = RUNS.RunStore(runs_root)
    try:
        entry = store.record(farm, out_json, fields_path=fields_path,
                             note="weekly")
        step("recorded", True, entry["id"])
    except Exception as e:                       # noqa: BLE001
        step("recorded", False, str(e))
        entry = None

    # 3. the comparison
    pair = store.pair_for_comparison(farm)
    if pair.get("ok"):
        cmp_ok = pair.get("comparable", {}).get("ok", True)
        if cmp_ok:
            previous = store.load(farm, pair["previous"]["id"])
            comparison = CH.compare(previous, report)
            result["change"] = {"headline": CH.headline(comparison),
                                "headline_ar": CH.headline(comparison, ar=True),
                                "counts": comparison["counts"],
                                "crossings": comparison["crossings"]}
            step("compared", True, CH.headline(comparison))
        else:
            step("compared", True,
                 "declined: " + pair["comparable"].get("reason", ""))
    else:
        step("compared", True, pair.get("reason", "nothing to compare"))

    # 4. the export
    if export_html:
        try:
            html_path = os.path.splitext(out_json)[0] + ".html"
            RH.write(html_path, report, field_fc, ar=ar)
            result["export"] = html_path
            step("exported", True, html_path)
        except Exception as e:                   # noqa: BLE001
            step("exported", False, str(e))

    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def digest(result: dict, ar: bool = False) -> str:
    """A few lines a person can read in a mail notification."""
    lines = [f"Farm Monitor - {result['farm']}",
             f"started {result.get('started_utc', '')}"]
    for s in result.get("steps", []):
        lines.append(f"  [{'ok ' if s['ok'] else 'FAIL'}] {s['step']}"
                     + (f" - {s['detail']}" if s["detail"] else ""))
    ch = result.get("change")
    if ch:
        lines.append("")
        lines.append(ch["headline_ar"] if ar else ch["headline"])
        for c in ch.get("crossings", []):
            lines.append(f"  ! {c['name']}: {c['from']} -> {c['to']}")
    if not result.get("ok"):
        lines.append("")
        lines.append("THIS RUN FAILED. The farm has not been analysed today.")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(
        description="One weekly cycle: run, record, compare, export.")
    p.add_argument("--farm", required=True,
                   help="the name this farm is filed under in the run store")
    p.add_argument("--fields", required=True)
    p.add_argument("--season", type=int, default=2022)
    p.add_argument("--crop", default="default")
    p.add_argument("--out", default="farm_report.json")
    p.add_argument("--runs", default="runs")
    p.add_argument("--observations", default="observations.db")
    p.add_argument("--no-export", action="store_true")
    p.add_argument("--english", action="store_true",
                   help="write the HTML export in English")
    p.add_argument("--show-schedule", action="store_true",
                   help="print the line that would schedule this, and exit "
                        "without running. This program does not install "
                        "scheduled tasks; that decision stays with you.")
    a = p.parse_args()

    if a.show_schedule:
        hint = schedule_hint(a.farm, a.fields, a.season)
        print(f"# {hint['note']}")
        print(hint["line"])
        return 0

    result = run_once(a.farm, a.fields, a.season, crop=a.crop,
                      out_json=a.out, runs_root=a.runs,
                      observations_db=a.observations,
                      export_html=not a.no_export, ar=not a.english)
    print("\n" + "=" * 72)
    print(digest(result))
    # The exit code is what a scheduler can see. A job that fails silently is
    # worse than no job: everybody believes the farm is being watched.
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
