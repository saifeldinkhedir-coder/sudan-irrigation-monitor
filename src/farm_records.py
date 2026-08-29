"""
Farm records: operations, costs, revenue, and the advisory built on top of them.

THE ONE RULE THIS MODULE ADDS TO THE PLATFORM
---------------------------------------------
Everything else in this engine is MEASURED - a satellite observed a surface and
an algorithm reduced it to a number. Everything in this module is REPORTED - a
person typed what they spent, what they applied, and when. Those are different
kinds of fact with different failure modes, and the platform must never let them
sit in the same table looking alike.

A measured NDVI is wrong when the atmosphere was wrong. A reported fertiliser
rate is wrong when someone misremembered, rounded, or had a reason to state a
different number. The second failure is not detectable from the data at all. So
every record here carries `provenance_kind: "REPORTED"`, every derived figure
that touches one is labelled the same way, and a figure that mixes the two
(gross margin per unit of water, say) is labelled MIXED and says which half came
from where.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
An "AI chat assistant" and a community forum are on the wish list this platform
was specified against, and neither is built here, for different reasons.

  The forum is a server, a moderation policy and a safety burden, not an
  analysis feature. Building it into an analysis engine would be a category
  error, and in a context where farmer-herder tension is live, an unmoderated
  forum is a genuine hazard rather than a missing nicety.

  A chat assistant that answers agronomic questions from a language model would
  be the single largest integrity hole in the platform: it would produce fluent,
  confident answers with no provenance, no NOT AVAILABLE, and no way to enforce
  any of the eight rules the rest of this code exists to enforce. The advisory
  below is rule-based instead: every sentence it emits is traceable to an
  indicator the engine computed, and when the indicator is missing it says
  nothing rather than something plausible.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional


# ==============================================================================
# CONFIGURATION
# ==============================================================================

OPERATION_TYPES = ("land_preparation", "planting", "irrigation", "fertiliser",
                   "pesticide", "weeding", "harvest", "transport", "other")

# Advisory thresholds. All ARBITRARY, all declared in the output.
ADVISORY_IRRIGATION_DEFICIT_MM = 25.0   # season deficit worth mentioning
ADVISORY_DRY_SPELL_DAYS = 10
ADVISORY_LOW_VIGOUR_PERCENTILE = "low"  # the relative band, not a raw NDVI


@dataclass
class Operation:
    """One thing that happened on a field, as reported by a person."""
    field_id: str
    date: str
    operation: str
    cost: float = 0.0
    currency: str = "SDG"
    quantity: Optional[float] = None
    unit: Optional[str] = None
    note: Optional[str] = None
    reported_by: Optional[str] = None
    op_id: str = ""
    recorded_at: str = ""

    def __post_init__(self):
        if not self.op_id:
            self.op_id = uuid.uuid4().hex[:12]
        if not self.recorded_at:
            self.recorded_at = datetime.now(timezone.utc).isoformat()
        if self.operation not in OPERATION_TYPES:
            raise ValueError(
                f"unknown operation {self.operation!r}; expected one of "
                f"{OPERATION_TYPES}")


@dataclass
class Sale:
    """Revenue from a field, as reported by a person."""
    field_id: str
    date: str
    quantity: float
    unit: str
    revenue: float
    currency: str = "SDG"
    sale_id: str = ""

    def __post_init__(self):
        if not self.sale_id:
            self.sale_id = uuid.uuid4().hex[:12]


class RecordStore:
    """
    Operations and sales for a field, and the gross margin they imply.

    Nothing here is verified against anything. That is stated in every result
    rather than assumed to be understood.
    """

    def __init__(self, path: str = "farm_records.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                op_id TEXT PRIMARY KEY, field_id TEXT NOT NULL,
                date TEXT NOT NULL, operation TEXT NOT NULL,
                cost REAL NOT NULL, currency TEXT NOT NULL,
                quantity REAL, unit TEXT, note TEXT, reported_by TEXT,
                recorded_at TEXT NOT NULL)""")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                sale_id TEXT PRIMARY KEY, field_id TEXT NOT NULL,
                date TEXT NOT NULL, quantity REAL NOT NULL, unit TEXT NOT NULL,
                revenue REAL NOT NULL, currency TEXT NOT NULL)""")
        self.conn.commit()

    def add_operation(self, op: Operation) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO operations (op_id, field_id, date, "
            "operation, cost, currency, quantity, unit, note, reported_by, "
            "recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (op.op_id, op.field_id, op.date, op.operation, op.cost, op.currency,
             op.quantity, op.unit, op.note, op.reported_by, op.recorded_at))
        self.conn.commit()
        return op.op_id

    def add_sale(self, sale: Sale) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO sales (sale_id, field_id, date, quantity, "
            "unit, revenue, currency) VALUES (?,?,?,?,?,?,?)",
            (sale.sale_id, sale.field_id, sale.date, sale.quantity, sale.unit,
             sale.revenue, sale.currency))
        self.conn.commit()
        return sale.sale_id

    def cost_breakdown(self, field_id: str, start: Optional[str] = None,
                       end: Optional[str] = None) -> dict:
        q = "SELECT operation, SUM(cost), COUNT(*), currency FROM operations WHERE field_id = ?"
        args = [field_id]
        if start:
            q += " AND date >= ?"
            args.append(start)
        if end:
            q += " AND date <= ?"
            args.append(end)
        q += " GROUP BY operation, currency"
        rows = self.conn.execute(q, args).fetchall()
        if not rows:
            return {"status": "NOT AVAILABLE",
                    "reason": f"no operations recorded for {field_id} in this window",
                    "provenance_kind": "REPORTED"}
        currencies = {r[3] for r in rows}
        if len(currencies) > 1:
            # Summing across currencies would need an exchange rate this module
            # does not have and must not invent.
            return {"status": "NOT AVAILABLE",
                    "reason": (f"operations are recorded in {sorted(currencies)}; "
                               "no exchange rate is available and none is assumed"),
                    "provenance_kind": "REPORTED"}
        return {
            "status": "OK",
            "field_id": field_id,
            "currency": rows[0][3],
            "by_operation": {r[0]: round(r[1], 2) for r in rows},
            "total_cost": round(sum(r[1] for r in rows), 2),
            "n_operations": sum(r[2] for r in rows),
            "provenance_kind": "REPORTED",
            "caveat": ("Self-reported by the farm, not verified against "
                       "anything. Unlike the satellite indicators, an error "
                       "here is not detectable from the data."),
        }

    def gross_margin(self, field_id: str, start: Optional[str] = None,
                     end: Optional[str] = None) -> dict:
        costs = self.cost_breakdown(field_id, start, end)
        q = "SELECT SUM(revenue), currency FROM sales WHERE field_id = ?"
        args = [field_id]
        if start:
            q += " AND date >= ?"
            args.append(start)
        if end:
            q += " AND date <= ?"
            args.append(end)
        q += " GROUP BY currency"
        rows = self.conn.execute(q, args).fetchall()

        if costs["status"] != "OK":
            return {"status": "NOT AVAILABLE",
                    "reason": f"costs unavailable: {costs['reason']}",
                    "provenance_kind": "REPORTED"}
        if not rows:
            return {"status": "NOT AVAILABLE",
                    "reason": ("costs are recorded but no sales are; a margin "
                               "without revenue would be a loss figure "
                               "masquerading as a result"),
                    "total_cost": costs["total_cost"],
                    "currency": costs["currency"],
                    "provenance_kind": "REPORTED"}
        if len(rows) > 1 or rows[0][1] != costs["currency"]:
            return {"status": "NOT AVAILABLE",
                    "reason": "costs and sales are in different currencies",
                    "provenance_kind": "REPORTED"}

        revenue = round(rows[0][0], 2)
        return {
            "status": "OK",
            "field_id": field_id,
            "currency": costs["currency"],
            "total_cost": costs["total_cost"],
            "total_revenue": revenue,
            "gross_margin": round(revenue - costs["total_cost"], 2),
            "provenance_kind": "REPORTED",
            "caveat": ("Both sides self-reported. This is bookkeeping, not a "
                       "measurement, and it is not comparable with the "
                       "satellite figures elsewhere in this report."),
        }

    def water_productivity(self, field_id: str, irrigation_requirement_mm,
                           field_area_ha: Optional[float],
                           start: Optional[str] = None,
                           end: Optional[str] = None) -> dict:
        """
        Gross margin per unit of water REQUIRED - a MIXED figure, and labelled
        as one.

        The numerator is reported by a person; the denominator is calculated
        from satellite and reanalysis data. That makes it more fragile than
        either input, and the label says which half came from where so nobody
        has to guess later.

        Note the denominator is requirement, not delivery. Nothing here knows
        how much water the field actually received.
        """
        margin = self.gross_margin(field_id, start, end)
        if margin["status"] != "OK":
            return {"status": "NOT AVAILABLE", "reason": margin["reason"],
                    "provenance_kind": "MIXED"}
        if not irrigation_requirement_mm or not field_area_ha:
            return {"status": "NOT AVAILABLE",
                    "reason": ("needs both an irrigation requirement and a field "
                               "area; one or both is missing"),
                    "provenance_kind": "MIXED"}
        # 1 mm over 1 ha = 10 m3
        volume_m3 = irrigation_requirement_mm * field_area_ha * 10.0
        if volume_m3 <= 0:
            return {"status": "NOT AVAILABLE",
                    "reason": "computed water volume is zero",
                    "provenance_kind": "MIXED"}
        return {
            "status": "OK",
            "margin_per_m3": round(margin["gross_margin"] / volume_m3, 4),
            "currency": margin["currency"],
            "water_volume_m3": round(volume_m3, 1),
            "provenance_kind": "MIXED",
            "provenance_detail": {
                "numerator": "REPORTED: farm-recorded costs and sales",
                "denominator": ("MEASURED/MODELLED: irrigation requirement from "
                                "FAO-56 ET0 and satellite NDVI"),
            },
            "caveat": ("The denominator is water REQUIRED, not water DELIVERED. "
                       "This is not water-use efficiency and must not be quoted "
                       "as it."),
        }

    def close(self):
        self.conn.close()


# ==============================================================================
# ADVISORY - rule-based, every sentence traceable to a computed indicator
# ==============================================================================

def advisory(field_record: dict, canal_record: Optional[dict] = None,
             lang: str = "ar") -> dict:
    """
    Turn the engine's indicators into things a farmer can act on.

    Every rule below fires only on an indicator the engine actually computed and
    marked OK. A missing indicator produces no sentence - not a hedged one, not
    a default one. The `withheld` list says what could not be spoken about and
    why, so silence is legible rather than ambiguous.

    This is deliberately not a chat assistant. A language model asked the same
    question would answer every time, which is precisely the failure this
    platform is built to avoid.
    """
    items = []      # (key, ar, en)
    withheld = []

    cond = (field_record or {}).get("condition", {}) or {}
    ind = cond.get("indicators", {}) or {}
    ctx = cond.get("context", {}) or {}
    water = (field_record or {}).get("water_requirement", {}) or {}
    nutrition = (field_record or {}).get("nutrition", {}) or {}
    ref = (field_record or {}).get("reference_provenance", {}) or {}

    # 1. Irrigation requirement - the actionable number
    if water.get("status") == "OK" and water.get("irrigation_requirement_mm"):
        mm = water["irrigation_requirement_mm"]
        if mm >= ADVISORY_IRRIGATION_DEFICIT_MM:
            items.append((
                "irrigation",
                f"احتاج محصولك نحو {mm:.0f} مم ماءً أكثر ممّا وفّره المطر هذا "
                "الموسم. هذا حساب للاحتياج، لا قياس لما وصلك فعلًا.",
                f"your crop needed about {mm:.0f} mm more water than rainfall "
                "supplied this season. This is a calculation of NEED, not a "
                "measurement of what reached you."))
    elif water.get("status") == "NOT AVAILABLE":
        withheld.append(("irrigation", water.get("reason", "not computed")))

    # 2. Stress verdict - only where a real reference existed
    if ref.get("verdict_withheld"):
        withheld.append(("stress", "no reference area wide enough for a threshold"))
    elif ctx.get("reading_status") == "OK" and ctx.get("reading"):
        items.append(("stress", f"قراءة الحالة: {ctx['reading']}",
                      f"condition reading: {ctx['reading']}"))

    # 3. Rainfall context - never a stress statement without it
    rain = ctx.get("rainfall_mm_last_14d")
    if rain is not None:
        items.append((
            "rainfall",
            f"سقط نحو {rain:.1f} مم مطرًا في الأسبوعين الأخيرين.",
            f"about {rain:.1f} mm of rain fell in the last 14 days."))
    else:
        withheld.append(("rainfall", "no CHIRPS rainfall figure for this window"))

    # 4. Nutrition - at whatever level the evidence supports, never higher
    if nutrition.get("status") == "OK":
        level = nutrition.get("claim_level", "relative")
        if level == "calibrated" and nutrition.get("nitrogen_pct") is not None:
            conf = nutrition.get("nitrogen_confidence", {}) or {}
            items.append((
                "nutrition",
                f"نيتروجين الورقة نحو {nutrition['nitrogen_pct']}% "
                f"(خطأ المعايرة {conf.get('rmse_pct')}%).",
                f"leaf nitrogen about {nutrition['nitrogen_pct']}% "
                f"(model RMSE {conf.get('rmse_pct')}%)."))
        elif level == "sufficiency" and nutrition.get("sufficiency_index"):
            items.append((
                "nutrition",
                f"مؤشّر الكفاية مقابل شريطك المرجعي: "
                f"{nutrition['sufficiency_index']}.",
                f"sufficiency against your reference strip: "
                f"{nutrition['sufficiency_index']}."))
        elif nutrition.get("relative_condition"):
            items.append((
                "nutrition",
                f"حالة الكلوروفيل مقارنة بالمخطط: "
                f"{nutrition['relative_condition']}. هذه مرتبة نسبية وليست "
                "قياسًا للنيتروجين.",
                f"chlorophyll condition within the scheme: "
                f"{nutrition['relative_condition']}. This is a relative rank, "
                "not a nitrogen measurement."))
    else:
        withheld.append(("nutrition", nutrition.get("reason", "not computed")))

    # 5. Canal context - the cause-separating half, stated without attribution
    if canal_record:
        cw = canal_record.get("canal_water", {}) or {}
        if cw.get("status") == "OK" and cw.get("value") is not None:
            pct = int(round(100 * cw["value"]))
            items.append((
                "canal",
                f"أظهر الرادار ماءً في نحو {pct}% من مجرى ترعتك هذا الموسم.",
                f"radar showed water in about {pct}% of your canal channel "
                "this season."))
        else:
            withheld.append(("canal", cw.get("reason", "canal water not measured")))

    pick = 1 if lang == "ar" else 2      # index into (key, ar, en)
    return {
        "field": (field_record or {}).get("name"),
        "language": lang,
        "items": [{"key": row[0], "text": row[pick]} for row in items],
        "withheld": [{"key": k, "reason": r} for k, r in withheld],
        "attributes_cause": False,
        "basis": {
            "irrigation_deficit_mm": ADVISORY_IRRIGATION_DEFICIT_MM,
            "note": ("ARBITRARY: the deficit worth mentioning is a hand-chosen "
                     "figure controlling how often this advisory speaks, and "
                     "carries no agronomic meaning."),
        },
        "rule": ("Every item is traceable to an indicator the engine computed "
                 "and marked OK. Nothing is inferred, generated or filled in. "
                 "A missing indicator produces silence, listed in `withheld`."),
    }
