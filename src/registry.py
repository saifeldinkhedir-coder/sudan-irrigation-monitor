"""
The scheme hierarchy: making the tool speak the institution's language.

WHY A HIERARCHY AND NOT A LIST OF FIELDS
----------------------------------------
The platform holds a flat list of polygons. The Gezira Scheme does not think in
flat lists: it is administered in nested units, and every question anybody with
authority asks is asked at one of those levels. "How is block 14 doing" is not a
question a flat list can answer without somebody exporting to a spreadsheet, and
a tool that needs a spreadsheet to answer the commonest question in the
institution is a tool that will be replaced by the spreadsheet.

Encoding the hierarchy costs little now. Retrofitting it after a season of
collected records is a data migration, and data migrations on a system holding
other people's tenancy records are how projects stop.

THE LEVEL NAMES ARE CONFIGURATION, NOT FACT
-------------------------------------------
The default below follows the structure commonly described for Gezira - group,
block, number, tenancy. I am not the authority on it: administrative naming has
changed with successive reorganisations, and a neighbouring scheme (Rahad, New
Halfa) divides differently. So the levels are DATA, set once for a deployment,
and the code makes no assumption about how many there are or what they are
called. CONFIRM THE ACTUAL NAMES AND DEPTH WITH THE SCHEME ADMINISTRATION
before a pilot; the wrong label on the right structure is worse than no label,
because it looks correct in a report.

A private farm with no hierarchy is a valid deployment: `FLAT`.

WHAT AGGREGATION WILL NOT DO
----------------------------
It will not average away an unmeasured field. Rolling forty fields into a block
figure where six could not be seen produces a number that describes
thirty-four; presenting it as the block's is the same lie the map's grey exists
to prevent, one level up. So every aggregate carries its own coverage, and a
level whose coverage falls below a floor reports the coverage instead of the
number.
"""

from __future__ import annotations

from typing import Optional, Sequence


# ==============================================================================
# LEVEL DEFINITIONS
# ==============================================================================

class Hierarchy:
    """
    The administrative levels of one deployment, outermost first.

    Each level is (key, arabic, english). `key` is what appears in a field's
    properties, so a field file written by the scheme's own office can be
    ingested without renaming anything.
    """

    def __init__(self, levels: Sequence[Sequence[str]], name: str = ""):
        self.levels = [tuple(x) for x in levels]
        self.name = name
        self.keys = [k for k, _a, _e in self.levels]

    def label(self, key: str, ar: bool = False) -> str:
        for k, a, e in self.levels:
            if k == key:
                return a if ar else e
        return key

    def depth(self) -> int:
        return len(self.levels)

    def path(self, props: dict) -> list:
        """
        The hierarchy path of one field, outermost first.

        Stops at the first missing level rather than skipping it. A field that
        declares a tenancy number but no block cannot be placed under a block,
        and inventing "unknown block" would put every such field in one
        imaginary unit that then appears in reports as a real one.
        """
        out = []
        for k in self.keys:
            v = (props or {}).get(k)
            if v in (None, ""):
                break
            out.append(str(v).strip())
        return out

    def placed(self, props: dict) -> bool:
        """True when the field is placed all the way down the hierarchy."""
        return len(self.path(props)) == self.depth()

    def path_string(self, props: dict, sep: str = " / ") -> str:
        return sep.join(self.path(props))

    def level_of(self, props: dict, level_key: str) -> Optional[str]:
        """The value of one named level, or None if the field never reaches
        it."""
        if level_key not in self.keys:
            return None
        idx = self.keys.index(level_key)
        p = self.path(props)
        return p[idx] if idx < len(p) else None

    def group_key(self, props: dict, level_key: str) -> Optional[str]:
        """A stable key for grouping at one level: the path down TO that level.

        Block "14" in group "Wad Habouba" and block "14" in group "Hosh" are
        different blocks, and grouping on the bare number would merge two real
        places into one row that describes neither.
        """
        if level_key not in self.keys:
            return None
        idx = self.keys.index(level_key)
        p = self.path(props)
        if idx >= len(p):
            return None
        return " / ".join(p[:idx + 1])


# The default. Confirm the real names and depth with the scheme before use.
GEZIRA = Hierarchy([
    ("group", "المجموعة", "group"),
    ("block", "القسم", "block"),
    ("number", "النمرة", "number"),
    ("tenancy", "الحواشة", "tenancy"),
], name="Gezira (CONFIRM level names with the scheme administration)")

# A farm with no administrative structure above the field. Not a degraded case:
# most users outside a scheme are this.
FLAT = Hierarchy([], name="no hierarchy - a single farm")

PRESETS = {"gezira": GEZIRA, "flat": FLAT}


def preset(name: Optional[str]) -> Hierarchy:
    return PRESETS.get(str(name or "flat").lower(), FLAT)


# ==============================================================================
# AGGREGATION
# ==============================================================================

# Below this fraction of fields measured, the aggregate is withheld and the
# coverage reported in its place. ARBITRARY: it controls how often a level
# speaks, and carries no statistical meaning.
MIN_COVERAGE = 0.6


def _vigour(rec: dict) -> Optional[float]:
    v = ((rec or {}).get("crop_health") or {}).get("readings", {}).get(
        "vigour", {})
    return v.get("value") if v.get("status") == "OK" else None


def _below_threshold(rec: dict) -> Optional[bool]:
    v = ((rec or {}).get("crop_health") or {}).get("readings", {}).get(
        "vigour", {})
    if v.get("status") != "OK" or v.get("value") is None:
        return None
    thr = v.get("threshold")
    return None if thr is None else v["value"] < thr


def aggregate(report: dict, hierarchy: Hierarchy, level_key: str,
              field_props: Optional[dict] = None,
              min_coverage: float = MIN_COVERAGE) -> dict:
    """
    Roll the field results up to one administrative level.

    `field_props` maps field name to its properties, for reports whose fields
    do not carry them. Fields that cannot be placed at this level are returned
    in `unplaced` - never silently dropped, and never bundled into a made-up
    unit.

    Each unit carries its coverage. Where coverage is below the floor, the mean
    is withheld: forty fields of which six could not be seen produce a number
    that describes thirty-four, and calling it the block's is the map's grey
    problem one level up.
    """
    props_by = field_props or {}
    units = {}
    unplaced = []

    for rec in report.get("fields", []):
        name = rec.get("name", "")
        props = {**(rec.get("properties") or {}), **(props_by.get(name) or {})}
        key = hierarchy.group_key(props, level_key)
        if key is None:
            unplaced.append({"name": name,
                             "reason": f"no {level_key} recorded for this field",
                             "path": hierarchy.path_string(props)})
            continue
        u = units.setdefault(key, {"key": key, "n_fields": 0, "vigours": [],
                                   "n_attention": 0, "n_unmeasured": 0,
                                   "fields": []})
        u["n_fields"] += 1
        u["fields"].append(name)
        v = _vigour(rec)
        if v is None:
            u["n_unmeasured"] += 1
        else:
            u["vigours"].append(v)
            if _below_threshold(rec):
                u["n_attention"] += 1

    out = []
    for u in units.values():
        measured = len(u["vigours"])
        coverage = measured / u["n_fields"] if u["n_fields"] else 0.0
        row = {
            "key": u["key"], "n_fields": u["n_fields"],
            "n_measured": measured, "n_unmeasured": u["n_unmeasured"],
            "n_attention": u["n_attention"],
            "coverage": round(coverage, 3),
            "fields": sorted(u["fields"]),
        }
        if measured and coverage >= min_coverage:
            row["mean_vigour"] = round(sum(u["vigours"]) / measured, 4)
            row["withheld"] = False
        else:
            row["mean_vigour"] = None
            row["withheld"] = True
            row["reason"] = (
                f"{measured} of {u['n_fields']} fields measured "
                f"({coverage:.0%}); below the {min_coverage:.0%} floor, a mean "
                "would describe the measured ones and be read as the unit's")
            row["reason_ar"] = (
                f"قيست {measured} من {u['n_fields']} حقلًا ({coverage:.0%})؛ "
                f"ودون أرضية {min_coverage:.0%} يصف المتوسّط المقيسةَ وحدها "
                "ويُقرأ على أنّه متوسّط الوحدة كلّها")
        out.append(row)

    # Worst first: units with fields below their own threshold, then by mean.
    out.sort(key=lambda r: (-r["n_attention"],
                            r["mean_vigour"] if r["mean_vigour"] is not None
                            else 9))
    return {
        "level": level_key,
        "level_label": hierarchy.label(level_key),
        "level_label_ar": hierarchy.label(level_key, ar=True),
        "units": out, "unplaced": unplaced,
        "n_units": len(out), "n_unplaced": len(unplaced),
        "basis": (f"ARBITRARY: a unit reports a mean only when at least "
                  f"{min_coverage:.0%} of its fields were measured. The figure "
                  "controls how often a level speaks and has no statistical "
                  "meaning."),
    }


def validate_placement(field_fc: dict, hierarchy: Hierarchy) -> dict:
    """
    Check a field file against the hierarchy BEFORE a run.

    Catching an unplaced field here costs a moment. Catching it after an
    Earth Engine run over four thousand fields costs the run.
    """
    ok, partial, none = [], [], []
    for f in (field_fc or {}).get("features", []):
        props = f.get("properties") or {}
        name = props.get("name", "")
        path = hierarchy.path(props)
        if hierarchy.depth() == 0:
            ok.append(name)
        elif len(path) == hierarchy.depth():
            ok.append(name)
        elif path:
            partial.append({"name": name, "reached": hierarchy.keys[len(path)],
                            "path": " / ".join(path)})
        else:
            none.append(name)
    return {
        "hierarchy": hierarchy.name, "depth": hierarchy.depth(),
        "fully_placed": ok, "partially_placed": partial, "unplaced": none,
        "n_total": len(ok) + len(partial) + len(none),
        "ready": not partial and not none,
        "note": ("Every field is placed." if not partial and not none else
                 "Fields that are not fully placed still get analysed - they "
                 "simply cannot be rolled up. Nothing is refused for this."),
        "note_ar": ("كل حقل موضوع في الهرم." if not partial and not none else
                    "الحقول غير الموضوعة تُحلَّل كالمعتاد — غير أنّها لا "
                    "تُجمَّع في وحدة. ولا يُرفض شيء بسبب هذا."),
    }
