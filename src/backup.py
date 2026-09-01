"""
Backing up the half of this system that cannot be recomputed.

WHAT IS AT RISK, AND WHAT IS NOT
--------------------------------
Every satellite figure in this platform can be regenerated: the imagery is
still up there, the engine is in version control, and a lost report costs an
afternoon of Earth Engine time. That half is safe by construction.

The other half is not. Thirty weighed harvests, thirty leaf-nitrogen samples,
a season of scouting photographs and a farmer's own cost records exist in
exactly one place - a SQLite file and a folder of images on one laptop. They
took a season of somebody's labour to collect, they are what unlocks the yield
model and the nitrogen figure, and if that disk fails they are gone. Not
degraded: gone.

So this backs up the REPORTED side and the run history, and says plainly that
it is not backing up the satellite side because it does not need to.

WHAT THIS IS NOT
----------------
It is a copy. A copy on the same laptop as the original protects against a
mistaken delete and against nothing else - not fire, not theft, not the disk.
The archive is written so it can be carried off the machine, and the tool says
so every time, because a backup nobody moves is a filing habit, not a backup.

WHY A ZIP AND A CHECKSUM
------------------------
One file to carry, and a way to know it arrived whole. A truncated copy of a
season of records looks exactly like a good one until the day it is needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zipfile
from datetime import datetime, timezone
from typing import Optional


# The things that took human labour to create. Everything else is derived.
IRREPLACEABLE = [
    ("farm_records.db", "operations, costs, sales - what the farmer recorded"),
    ("observations.db", "scouting: photographs, findings, and the "
                        "satellite-vs-ground comparisons"),
    ("calibration.db", "leaf nitrogen and weighed harvests - what unlocks the "
                       "gated figures"),
    ("yield_calibration.db", "the fitted yield model and its points"),
]

REPLACEABLE_NOTE = (
    "Satellite reports are NOT in this archive and do not need to be: the "
    "imagery is still in orbit and the engine is in version control, so a lost "
    "report costs an afternoon of compute. What is here is the half that took "
    "a season of somebody's labour and exists nowhere else.")

REPLACEABLE_NOTE_AR = (
    "تقارير الأقمار ليست في هذه النسخة ولا تحتاج أن تكون: الصور ما زالت في "
    "مدارها والمحرّك في نظام الإصدارات، فالتقرير الضائع يكلّف عصر يوم حسابًا. "
    "وما هنا هو النصف الذي كلّف موسمًا من عمل إنسان ولا وجود له في مكان آخر.")

OFFSITE_WARNING = (
    "This archive is on the same machine as the originals. That protects "
    "against a mistaken delete and against nothing else. Copy it somewhere "
    "that is not this laptop.")

OFFSITE_WARNING_AR = (
    "هذه النسخة على الجهاز نفسه الذي عليه الأصل. وهي تحمي من حذف بالخطأ ولا "
    "تحمي من شيء آخر. انسخها إلى مكان ليس هذا الحاسوب.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _row_counts(db_path: str) -> dict:
    """How many rows are in each table.

    Recorded so a restored archive can be checked against what was backed up.
    A file that opens and is empty is the failure mode that goes unnoticed.
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return {"error": str(e)[:120]}
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        out = {}
        for t in tables:
            try:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
            except sqlite3.Error:
                out[t] = None
        return out
    finally:
        conn.close()


def survey(src_dir: str = ".", obs_dir: str = "observations") -> dict:
    """
    What is here to lose, before anything is copied.

    Shown before the backup runs, because "you have 41 scouting photographs and
    30 weighed harvests" is what makes somebody actually carry the file off the
    machine.
    """
    found, missing = [], []
    for name, why in IRREPLACEABLE:
        p = os.path.join(src_dir, name)
        if os.path.exists(p):
            found.append({"file": name, "why": why,
                          "bytes": os.path.getsize(p),
                          "rows": _row_counts(p)})
        else:
            missing.append({"file": name, "why": why})

    photos = []
    pdir = os.path.join(src_dir, obs_dir)
    if os.path.isdir(pdir):
        for root, _d, files in os.walk(pdir):
            for f in files:
                photos.append(os.path.join(root, f))

    return {"found": found, "missing": missing,
            "n_photographs": len(photos),
            "photograph_bytes": sum(os.path.getsize(p) for p in photos),
            "note": REPLACEABLE_NOTE, "note_ar": REPLACEABLE_NOTE_AR}


def create(dest: str, src_dir: str = ".", obs_dir: str = "observations",
           runs_dir: str = "runs") -> dict:
    """
    Write the archive, and a manifest describing exactly what went in.

    The manifest carries a checksum per file. A truncated copy of a season of
    records looks exactly like a good one until the day it is needed, and that
    day is the worst possible time to find out.
    """
    s = survey(src_dir, obs_dir)
    entries = []
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for item in s["found"]:
            p = os.path.join(src_dir, item["file"])
            z.write(p, arcname=item["file"])
            entries.append({"path": item["file"], "sha256": sha256(p),
                            "bytes": item["bytes"], "rows": item["rows"]})

        pdir = os.path.join(src_dir, obs_dir)
        if os.path.isdir(pdir):
            for root, _d, files in os.walk(pdir):
                for f in files:
                    p = os.path.join(root, f)
                    arc = os.path.relpath(p, src_dir).replace("\\", "/")
                    z.write(p, arcname=arc)
                    entries.append({"path": arc, "sha256": sha256(p),
                                    "bytes": os.path.getsize(p)})

        # The run history goes in too. It cannot be recomputed either: a run is
        # a measurement of a date that has passed.
        rdir = os.path.join(src_dir, runs_dir)
        if os.path.isdir(rdir):
            for root, _d, files in os.walk(rdir):
                for f in files:
                    p = os.path.join(root, f)
                    arc = os.path.relpath(p, src_dir).replace("\\", "/")
                    z.write(p, arcname=arc)
                    entries.append({"path": arc, "sha256": sha256(p),
                                    "bytes": os.path.getsize(p)})

        manifest = {
            "created_utc": _now(), "source": os.path.abspath(src_dir),
            "n_files": len(entries), "files": entries,
            "not_included": REPLACEABLE_NOTE,
            "not_included_ar": REPLACEABLE_NOTE_AR,
            "warning": OFFSITE_WARNING, "warning_ar": OFFSITE_WARNING_AR,
        }
        z.writestr("BACKUP_MANIFEST.json",
                   json.dumps(manifest, indent=2, ensure_ascii=False))

    return {"path": dest, "bytes": os.path.getsize(dest),
            "n_files": len(entries), "manifest": manifest,
            "warning": OFFSITE_WARNING, "warning_ar": OFFSITE_WARNING_AR}


def verify(archive: str) -> dict:
    """
    Check an archive against its own manifest.

    An untested backup is a belief, not a backup. This is cheap enough to run
    every time one is made, and it is the only thing that separates the two.
    """
    if not os.path.exists(archive):
        return {"ok": False, "reason": "archive not found"}
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        if "BACKUP_MANIFEST.json" not in names:
            return {"ok": False,
                    "reason": "no manifest - this was not written by this tool, "
                              "so there is nothing to check it against"}
        man = json.loads(z.read("BACKUP_MANIFEST.json").decode("utf-8"))
        bad, missing = [], []
        for f in man.get("files", []):
            if f["path"] not in names:
                missing.append(f["path"])
                continue
            got = hashlib.sha256(z.read(f["path"])).hexdigest()
            if f.get("sha256") and got != f["sha256"]:
                bad.append(f["path"])
    ok = not bad and not missing
    return {"ok": ok, "n_files": len(man.get("files", [])),
            "corrupt": bad, "missing": missing,
            "created_utc": man.get("created_utc"),
            "reason": ("" if ok else
                       f"{len(missing)} missing, {len(bad)} failed their "
                       "checksum")}
