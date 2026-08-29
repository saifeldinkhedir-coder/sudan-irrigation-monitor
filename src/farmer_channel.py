"""
Farmer channel (Stage 4 floor) - the simplest thing that reaches a farmer
reliably: one sentence, per reach, that they can carry into a meeting.

WHY A ONE-SENTENCE CARD, AND WHY THIS IS THE *FLOOR*
----------------------------------------------------
The rich channel is the GeoLibre app (map, offline, two-way field collection).
But the design criterion for the FIRST channel is reliability of REACH, and a
one-way message (SMS / WhatsApp text, or a slip printed at the scheme office)
reaches more reliably than anything that needs an app session on a specific
device with connectivity at a specific moment. The capable farmers who install
the app get the rich channel; this card is the floor that reaches everyone.

THE RULES THIS GENERATOR ENFORCES
---------------------------------
- Every clause is a MEASURED quantity. Nothing is stated that the engine marked
  NOT AVAILABLE - that clause is simply omitted, never filled with a zero or a
  guess.
- An UNRELIABLE gap (near-zero head vigour) yields no percentage.
- It ATTRIBUTES NOTHING. "Your reach is X% below the canal head" is a measured
  difference; there is no sentence blaming an office, an operator or a decision.
  This is integrity rule 5, carried all the way to the farmer.
- It is the same number the manager and researcher see, phrased for the farmer -
  one engine, three phrasings.

Pure and testable: no Earth Engine, no network.
"""

from __future__ import annotations

from typing import Optional


def _pct(frac: float) -> int:
    return int(round(100 * frac))


def _nearest_reach(reaches: list, position: float) -> Optional[dict]:
    if not reaches:
        return None
    return min(reaches, key=lambda r: abs(r.get("position_along_canal", 0) - position))


def farmer_card(canal_record: dict, reach_position: Optional[float] = None,
                lang: str = "ar", season_label: str = "") -> dict:
    """
    Build a one-sentence card for a farmer on a given canal (and optionally a
    given reach position 0=head..1=tail).

    Returns {"text": str, "clauses": [...], "attributes_cause": False}. The
    clauses list is what was actually said, so a caller can see which facts were
    available and which were withheld.
    """
    name = canal_record.get("name", "")
    eq = canal_record.get("head_tail_equity", {}) or {}
    cw = canal_record.get("canal_water", {}) or {}
    climate = canal_record.get("climate", {}) or {}

    clauses = []          # (key, ar, en)

    # 1) vigour vs the canal head
    reliable = eq.get("gap_reliable", True)
    if eq.get("status") == "OK" and reliable:
        head = eq.get("head_fit_ndvi")
        if reach_position is not None and eq.get("reaches"):
            r = _nearest_reach(eq["reaches"], reach_position)
            if r and head:
                drop = (head - r["mean_ndvi"]) / head
                p = _pct(drop)
                if p >= 5:
                    clauses.append(("vigour",
                        f"مؤشّر نموّ محصولك عند موقعك أقل بنحو {p}% من رأس الترعة",
                        f"crop vigour at your location is about {p}% below the "
                        "head of the canal"))
                elif p <= -5:
                    clauses.append(("vigour",
                        f"مؤشّر نموّ محصولك عند موقعك أعلى بنحو {abs(p)}% من رأس الترعة",
                        f"crop vigour at your location is about {abs(p)}% above "
                        "the head of the canal"))
                else:
                    clauses.append(("vigour",
                        "مؤشّر نموّ محصولك قريب من مستوى رأس الترعة",
                        "crop vigour at your location is close to the canal head"))
        else:
            gap = eq.get("head_tail_gap")
            if gap is not None and gap >= 0.05:
                clauses.append(("vigour",
                    f"مؤشّر نموّ المحصول عند ذيل الترعة أقل بنحو {_pct(gap)}% من رأسها",
                    f"crop vigour at the tail of the canal is about {_pct(gap)}% "
                    "below the head"))

    # 2) canal water presence (Sentinel-1) - honest about what radar measures
    if cw.get("status") == "OK" and cw.get("value") is not None:
        wp = _pct(cw["value"])
        clauses.append(("water",
            f"وأظهر الرادار وجود ماء في نحو {wp}% من مجرى الترعة عند موقعك هذا الموسم",
            f"radar showed water present across about {wp}% of the canal channel "
            "at your location this season"))
    elif cw.get("status") in ("NOT AVAILABLE", "INSUFFICIENT DATA"):
        clauses.append(("water",
            "ولم يتيسّر قياس ماء الترعة هذا الموسم (صور رادار غير كافية)",
            "canal water could not be measured this season (too few radar images)"))

    # 3) rainfall context (so a farmer can see drought vs a network question)
    svh = climate.get("season_vs_history") if isinstance(climate, dict) else None
    if svh and svh.get("verdict"):
        v = svh["verdict"]
        ar_map = {
            "MUCH DRIER than this site's recent seasons": "وكان المطر هذا الموسم أقلّ بكثير من المعتاد لموقعك",
            "drier than usual": "وكان المطر هذا الموسم أقلّ من المعتاد",
            "near this site's normal": "وكان المطر هذا الموسم قريبًا من معدّل موقعك",
            "wetter than usual": "وكان المطر هذا الموسم أكثر من المعتاد",
            "MUCH WETTER than usual": "وكان المطر هذا الموسم أكثر بكثير من المعتاد",
        }
        clauses.append(("rain", ar_map.get(v, "وسياق المطر متاح في التقرير"),
                        f"rainfall this season was {v.lower()}"))

    if not clauses:
        return {
            "text": ("لا تتوفّر قياسات كافية لموقعك هذا الموسم." if lang == "ar"
                     else "Not enough measurements are available for your "
                          "location this season."),
            "clauses": [], "attributes_cause": False}

    season = f" ({season_label})" if season_label else ""
    if lang == "ar":
        prefix = "" if ("ترعة" in name or not name) else "ترعة "
        head = f"{prefix}{name}{season}: " if name else ""
        parts = [c[1] for c in clauses]
        # the connective "و" is correct between clauses but wrong at the start.
        if parts and parts[0].startswith("و"):
            parts[0] = parts[0][1:].lstrip()
        body = "، ".join(parts)
        text = head + body + "."
    else:
        prefix = "" if "canal" in name.lower() or not name else "Canal "
        head = f"{prefix}{name}{season}: " if name else ""
        body = "; ".join(c[2] for c in clauses)
        text = head + body + "."

    return {"text": text, "clauses": [c[0] for c in clauses],
            "attributes_cause": False}


# A blunt guard used by the tests: words that would turn a measured difference
# into an accusation. The generator must never emit any of these.
ATTRIBUTION_WORDS_AR = ["المسؤول", "الإدارة", "قرار", "تعمّد", "أهمل", "حرمك", "سرقة"]
ATTRIBUTION_WORDS_EN = ["blame", "denied", "stole", "negligent", "deliberately",
                        "official", "manager decided", "mismanaged"]
