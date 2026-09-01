"""
The run store: every analysis this farm has ever had, in one place.

WHY THIS IS ARCHITECTURE AND NOT CONVENIENCE
--------------------------------------------
The change page asked the reader to TYPE THE PATH of an older report. That is
not a rough edge in the interface; it is the interface admitting there is no
history. Everything a monitoring tool is for - what moved, what is trending,
whether last month's intervention worked - needs a series of runs, and a series
of runs needs somewhere to be.

It also decides something quietly: with a store, the honest comparison (the
previous run over the SAME farm) is the default, and the reader has to work to
compare the wrong things. Without one, the reader has to work to compare the
right things, and most will not.

WHAT IT REFUSES
---------------
It refuses to compare two runs over different farms. Two reports whose field
names barely overlap will produce a comparison full of "new" and "missing"
fields that means nothing, and the reader will read it as churn on their own
land. The overlap is measured and a comparison with too little of it is
declined with the number.

IT IS NOT A DATABASE
--------------------
Directories and JSON, deliberately. This has to run on a laptop in a field
office, be copied to a USB stick, survive being emailed, and be readable in
five years by somebody who has never heard of this program. A schema migration
on a server nobody can reach is how a season of records is lost.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Optional


MANIFEST = "manifest.json"

# Below this fraction of shared field names, two reports are not two runs over
# one farm. ARBITRARY: it controls when a comparison is declined.
MIN_OVERLAP = 0.5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe(name: str) -> str:
    """A directory name from a farm name. Conservative: this becomes a path."""
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in str(name).strip()]
    out = "".join(keep).strip("_") or "farm"
    return out[:64]


def digest(path: str) -> str:
    """A content hash, so a run can say which field file it rested on and a
    later reader can tell whether the boundaries have changed since."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class RunStore:
    """Runs for one deployment, laid out as runs/<farm>/<timestamp>.json."""

    def __init__(self, root: str = "runs"):
        self.root = root

    # ------------------------------------------------------------- writing
    def record(self, farm: str, report_path: str,
               fields_path: Optional[str] = None,
               note: str = "") -> dict:
        """
        Copy a finished report into the store and index it.

        The report is COPIED rather than moved or referenced. A run indexed by
        path stops existing the moment somebody tidies their desktop, and the
        history is the one thing here that cannot be recomputed.
        """
        if not os.path.exists(report_path):
            raise FileNotFoundError(report_path)
        farm_dir = os.path.join(self.root, _safe(farm))
        os.makedirs(farm_dir, exist_ok=True)

        with open(report_path, encoding="utf-8") as fh:
            report = json.load(fh)

        # The id is a second-resolution timestamp, so two runs started within
        # the same second would collide and the second would silently replace
        # the first. In production runs are minutes apart; in a test loop, and
        # on the day somebody re-runs immediately after a failure, they are
        # not - and losing a run from the history is losing the one thing here
        # that cannot be recomputed.
        stamp = _stamp()
        man = self._manifest(farm_dir)
        taken = {r["id"] for r in man.get("runs", [])}
        if stamp in taken or os.path.exists(os.path.join(farm_dir,
                                                         f"{stamp}.json")):
            n = 2
            while f"{stamp}-{n}" in taken or os.path.exists(
                    os.path.join(farm_dir, f"{stamp}-{n}.json")):
                n += 1
            stamp = f"{stamp}-{n}"
        stored = os.path.join(farm_dir, f"{stamp}.json")
        shutil.copyfile(report_path, stored)

        entry = {
            "id": stamp,
            "file": os.path.basename(stored),
            "recorded_utc": _now(),
            "generated_utc": report.get("generated_utc"),
            "season": report.get("season"),
            "crop": report.get("crop"),
            "crops_present": report.get("crops_present"),
            "n_fields": report.get("n_fields", len(report.get("fields", []))),
            "field_names": sorted(f.get("name", "")
                                  for f in report.get("fields", [])),
            "fields_file": fields_path,
            "fields_digest": (digest(fields_path)
                              if fields_path and os.path.exists(fields_path)
                              else None),
            "note": note,
        }
        man["farm"] = farm
        man["runs"] = [r for r in man.get("runs", []) if r["id"] != stamp]
        man["runs"].append(entry)
        man["runs"].sort(key=lambda r: r["id"])
        self._write_manifest(farm_dir, man)
        return entry

    # ------------------------------------------------------------- reading
    def farms(self) -> list:
        if not os.path.isdir(self.root):
            return []
        out = []
        for d in sorted(os.listdir(self.root)):
            man = os.path.join(self.root, d, MANIFEST)
            if os.path.exists(man):
                with open(man, encoding="utf-8") as fh:
                    out.append({"dir": d, **{k: v for k, v in
                                             json.load(fh).items()
                                             if k != "runs"}})
        return out

    def runs(self, farm: str) -> list:
        return self._manifest(os.path.join(self.root, _safe(farm))).get(
            "runs", [])

    def path_of(self, farm: str, run_id: str) -> str:
        return os.path.join(self.root, _safe(farm), f"{run_id}.json")

    def load(self, farm: str, run_id: str) -> dict:
        with open(self.path_of(farm, run_id), encoding="utf-8") as fh:
            return json.load(fh)

    def latest(self, farm: str) -> Optional[dict]:
        rs = self.runs(farm)
        return rs[-1] if rs else None

    def previous(self, farm: str) -> Optional[dict]:
        """The run before the latest - the default comparison, so the honest
        one is what happens when nobody chooses."""
        rs = self.runs(farm)
        return rs[-2] if len(rs) >= 2 else None

    def comparable(self, a: dict, b: dict,
                   min_overlap: float = MIN_OVERLAP) -> dict:
        """
        Are these two runs over the same farm?

        Two reports whose field names barely overlap produce a comparison full
        of "new" and "missing" fields that means nothing, and a reader will
        take it as churn on their own land.
        """
        sa, sb = set(a.get("field_names") or []), set(b.get("field_names") or [])
        if not sa or not sb:
            return {"ok": False, "overlap": 0.0,
                    "reason": "one of the runs lists no fields"}
        shared = sa & sb
        overlap = len(shared) / max(len(sa), len(sb))
        ok = overlap >= min_overlap
        return {
            "ok": ok, "overlap": round(overlap, 3),
            "n_shared": len(shared), "n_a": len(sa), "n_b": len(sb),
            "reason": ("" if ok else
                       f"only {len(shared)} field names are shared out of "
                       f"{max(len(sa), len(sb))} ({overlap:.0%}); these look "
                       "like two different farms rather than two runs over one"),
            "reason_ar": ("" if ok else
                          f"لا يشترك التشغيلان إلّا في {len(shared)} اسمًا من "
                          f"{max(len(sa), len(sb))} ({overlap:.0%})؛ وهذان "
                          "يبدوان مزرعتين مختلفتين لا تشغيلين لمزرعة واحدة"),
            # A boundary file that changed between runs is not a reason to
            # refuse - fields get redrawn - but it IS a reason to say so,
            # because a "change" in a redrawn field is partly the redrawing.
            "boundaries_changed": bool(
                a.get("fields_digest") and b.get("fields_digest")
                and a["fields_digest"] != b["fields_digest"]),
        }

    def pair_for_comparison(self, farm: str) -> dict:
        """The default comparison for this farm: previous against latest."""
        latest, prev = self.latest(farm), self.previous(farm)
        if not latest:
            return {"ok": False, "reason": "no runs recorded for this farm"}
        if not prev:
            return {"ok": False, "latest": latest,
                    "reason": "only one run recorded - there is nothing to "
                              "compare it with yet",
                    "reason_ar": "تشغيل واحد فقط مسجّل — ولا شيء يُقارن به بعد"}
        return {"ok": True, "previous": prev, "latest": latest,
                **{"comparable": self.comparable(prev, latest)}}

    # ------------------------------------------------------------ internals
    def _manifest(self, farm_dir: str) -> dict:
        p = os.path.join(farm_dir, MANIFEST)
        if not os.path.exists(p):
            return {"runs": []}
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def _write_manifest(self, farm_dir: str, man: dict) -> None:
        man["updated_utc"] = _now()
        with open(os.path.join(farm_dir, MANIFEST), "w",
                  encoding="utf-8") as fh:
            json.dump(man, fh, indent=2, ensure_ascii=False)
