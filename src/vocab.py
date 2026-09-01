"""
Engine vocabulary: the words the engine writes that a reader has to read.

WHY THIS MODULE EXISTS
----------------------
The engine writes its verdicts in English - "warmer than the surrounding
land", "clay", "drier than usual" - because that is the language the code is
written in. Those strings then travel all the way to a farmer's screen, so
every surface that displays them needs the same translation table.

There were two such surfaces and one table. The farmer app had it; the
printable report, written later, did not, and an Arabic sheet came out of the
printer reading "حرارة السطح 42.41 °C warmer than the surrounding land" and
"قوام التربة clay". Not broken enough to fail a test, and exactly wrong enough
to look unfinished to the person it was printed for.

So the table lives here, once, in `src` where both the engine-side and the
app-side can reach it, and every new surface gets it by importing rather than
by remembering.

THE RULE FOR EVERY TABLE BELOW
------------------------------
An unrecognised value passes through VERBATIM. When the engine gains a new
verdict, it appears on screen in English - visible, and fixable in one place.
The alternative, blanking it, hides the gap and shows a dash where a finding
should be.

WHAT IS DELIBERATELY NOT TRANSLATED
-----------------------------------
Sensor names, dataset identifiers, units and dates. "Sentinel-2", "100 m" and
"2022-07-01" are identifiers to be checked against a catalogue, not prose to be
read, and translating them makes provenance harder to verify rather than easier
to read.
"""

from __future__ import annotations


THERMAL_READING = {
    "warmer than the surrounding land": ("أدفأ من الأرض المحيطة",
                                         "warmer than the surrounding land"),
    "cooler than the surrounding land": ("أبرد من الأرض المحيطة",
                                         "cooler than the surrounding land"),
    "close to the surrounding land": ("قريب من الأرض المحيطة",
                                      "close to the surrounding land"),
}

SEASON_VERDICT = {
    "MUCH DRIER than this site's recent seasons":
        ("أجفّ بكثير من مواسم هذا الموقع الأخيرة",
         "MUCH DRIER than this site's recent seasons"),
    "drier than usual": ("أجفّ من المعتاد", "drier than usual"),
    "near this site's normal": ("قريب من معدّل هذا الموقع",
                                "near this site's normal"),
    "wetter than usual": ("أمطر من المعتاد", "wetter than usual"),
    "MUCH WETTER than usual": ("أمطر بكثير من المعتاد",
                               "MUCH WETTER than usual"),
}

RELATIVE_CONDITION = {
    "BELOW SCHEME NORM": ("دون معدّل المخطط", "BELOW SCHEME NORM"),
    "WITHIN SCHEME NORM": ("ضمن معدّل المخطط", "WITHIN SCHEME NORM"),
    "ABOVE SCHEME NORM": ("فوق معدّل المخطط", "ABOVE SCHEME NORM"),
}

SUFFICIENCY_READING = {
    "deficient": ("ناقص", "deficient"),
    "marginal": ("حدّي", "marginal"),
    "sufficient": ("كافٍ", "sufficient"),
}

# USDA texture classes. Arabic soil vocabulary is not uniform across the
# region; these follow the terms used in Sudanese agricultural extension -
# طَفال for loam, طَمْي for silt - rather than a transliteration.
SOIL_TEXTURE = {
    "clay": ("طين", "clay"),
    "silty clay": ("طين طَمْيي", "silty clay"),
    "sandy clay": ("طين رملي", "sandy clay"),
    "clay loam": ("طَفال طيني", "clay loam"),
    "silty clay loam": ("طَفال طيني طَمْيي", "silty clay loam"),
    "sandy clay loam": ("طَفال طيني رملي", "sandy clay loam"),
    "loam": ("طَفال", "loam"),
    "silt loam": ("طَفال طَمْيي", "silt loam"),
    "sandy loam": ("طَفال رملي", "sandy loam"),
    "silt": ("طَمْي", "silt"),
    "loamy sand": ("رمل طَفالي", "loamy sand"),
    "sand": ("رمل", "sand"),
    "unknown": ("غير معروف", "unknown"),
}

# Every table in this module, so a test can walk them all rather than listing
# them and missing the next one added.
TABLES = {
    "THERMAL_READING": THERMAL_READING,
    "SEASON_VERDICT": SEASON_VERDICT,
    "RELATIVE_CONDITION": RELATIVE_CONDITION,
    "SUFFICIENCY_READING": SUFFICIENCY_READING,
    "SOIL_TEXTURE": SOIL_TEXTURE,
}


def tr(table: dict, key, ar: bool, default: str = "—") -> str:
    """Translate a known engine value.

    Anything unrecognised passes through verbatim, so a new engine verdict is
    visible in English rather than silently blanked.
    """
    if key is None:
        return default
    pair = table.get(key)
    if not pair:
        return str(key)
    return pair[0] if ar else pair[1]
