"""
Making a long run survivable.

THE FAILURE THIS EXISTS FOR
---------------------------
An Earth Engine run over a scheme is thousands of round trips over a
connection that is not reliable and against a quota that is not unlimited. It
fails at field 3,700 of 4,000 - on a timeout, a quota, or somebody closing a
laptop - and everything is lost, including the 3,699 fields that succeeded.
The person then has to decide whether to spend another three hours, and the
honest answer is often no. So the tool that could have monitored the scheme
does not, for a reason that has nothing to do with remote sensing.

Writing each field's result as it arrives turns that from a lost run into a
resumed one.

WHY THE INPUT IS FINGERPRINTED
------------------------------
Resuming is only safe while the question has not changed. A checkpoint written
for last season, or for a different set of boundaries, or before somebody
corrected a crop label, describes a different run - and silently merging it
into this one would produce a report that is half one thing and half another,
with nothing on its face to say so. That is worse than losing the run, because
it is wrong rather than absent.

So the checkpoint carries a fingerprint of everything that determines the
answer: the boundaries, the season, the crop, and whether the time series was
asked for. A mismatch discards the checkpoint and SAYS SO, rather than starting
fresh in silence and leaving somebody wondering why it took three hours again.

WHY THE PARTIAL FILE IS NOT THE REPORT
--------------------------------------
It is written to `<out>.partial` and removed on success. A half-finished report
sitting at the report's own path would be read as a report - by a person, by
the run store, by the change page - and a farm whose worst fields happened to
be analysed last would appear to be doing fine.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional


SUFFIX = ".partial"


def fingerprint(field_fc: dict, season: int, crop: str,
                with_series: bool) -> str:
    """
    Everything that determines the answer, in one string.

    The geometry is included, not just the field count: two runs over four
    fields whose boundaries were redrawn between them are different runs, and
    a count would not notice.
    """
    payload = {
        "season": int(season), "crop": str(crop),
        "with_series": bool(with_series),
        "fields": sorted(
            [(f.get("properties", {}).get("name", ""),
              json.dumps(f.get("geometry"), sort_keys=True),
              json.dumps({k: v for k, v in (f.get("properties") or {}).items()
                          if k != "name"}, sort_keys=True, ensure_ascii=False))
             for f in (field_fc or {}).get("features", [])]),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class Checkpoint:
    """
    A partial run on disk.

    Usage from the engine:

        cp = Checkpoint(out_json, fingerprint(...))
        done = cp.resume()             # {} on a fresh or mismatched run
        for field in fields:
            if field.name in done:
                continue
            ...
            cp.add(record)
        cp.done()
    """

    def __init__(self, out_json: str, fp: str, enabled: bool = True):
        self.path = out_json + SUFFIX
        self.fp = fp
        self.enabled = enabled
        self.records: list = []
        self.status = "FRESH"
        self.note = ""
        self.note_ar = ""

    # ---------------------------------------------------------------- resume
    def resume(self, restart: bool = False) -> dict:
        """
        Completed field records from a previous attempt, by name.

        Returns {} and explains itself in `status`/`note` whenever it declines
        to resume. It never resumes silently into a different question.
        """
        if not self.enabled or restart or not os.path.exists(self.path):
            if restart and os.path.exists(self.path):
                self.status = "DISCARDED"
                self.note = "restart requested; the checkpoint was discarded"
                self.note_ar = "طُلب البدء من جديد، فأُهملت نقطة الحفظ"
            return {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            # A truncated checkpoint is common: the process died mid-write.
            # Say so and start over rather than crashing on somebody's data.
            self.status = "UNREADABLE"
            self.note = f"the checkpoint could not be read ({e}); starting over"
            self.note_ar = "تعذّرت قراءة نقطة الحفظ، فالبدء من جديد"
            return {}

        if data.get("fingerprint") != self.fp:
            self.status = "STALE"
            self.note = (
                "the checkpoint was written for a different run - the "
                "boundaries, the season, the crop or the series setting has "
                "changed since - so it was discarded rather than merged. "
                "Merging would produce a report that is half one question and "
                "half another, with nothing on its face to say so.")
            self.note_ar = (
                "كُتبت نقطة الحفظ لتشغيل آخر — تغيّرت الحدود أو الموسم أو "
                "المحصول أو إعداد السلسلة — فأُهملت ولم تُدمج. والدمج ينتج "
                "تقريرًا نصفه سؤال ونصفه سؤال آخر، ولا شيء في ظاهره يقول ذلك.")
            return {}

        self.records = list(data.get("fields", []))
        self.status = "RESUMED"
        n = len(self.records)
        self.note = (f"resuming: {n} field"
                     f"{'s' if n != 1 else ''} already analysed on "
                     f"{data.get('updated_utc', 'an earlier attempt')}")
        self.note_ar = (f"استئناف: {n} حقلًا حُلِّلت في محاولة سابقة")
        return {r.get("name"): r for r in self.records if r.get("name")}

    # ----------------------------------------------------------------- write
    def add(self, record: dict) -> None:
        """Append one field's result and flush.

        Flushed per field rather than per batch: the whole point is to survive
        a process that stops without warning, and a buffer is exactly the thing
        that does not survive that.
        """
        if not self.enabled:
            return
        self.records.append(record)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"fingerprint": self.fp,
                       "updated_utc": datetime.now(timezone.utc).isoformat(),
                       "n_fields": len(self.records),
                       "fields": self.records}, fh, ensure_ascii=False)
        # Written to a temporary file and moved into place, so a crash during
        # the write leaves the last good checkpoint rather than a truncated one.
        os.replace(tmp, self.path)

    def done(self) -> None:
        """Remove the partial file once the real report has been written."""
        for p in (self.path, self.path + ".tmp"):
            try:
                os.remove(p)
            except OSError:
                pass

    # ------------------------------------------------------------- reporting
    def describe(self) -> dict:
        return {"status": self.status, "note": self.note,
                "note_ar": self.note_ar, "path": self.path,
                "n_recovered": len(self.records)}


def find_partial(out_json: str) -> Optional[dict]:
    """Look at a checkpoint without resuming it - for the app, which wants to
    offer the choice rather than make it."""
    p = out_json + SUFFIX
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"readable": False, "path": p}
    return {"readable": True, "path": p,
            "n_fields": data.get("n_fields", len(data.get("fields", []))),
            "updated_utc": data.get("updated_utc"),
            "fingerprint": data.get("fingerprint")}
