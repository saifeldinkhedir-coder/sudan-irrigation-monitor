"""
The crop library: one place that knows what a crop is.

WHY THIS MODULE EXISTS
----------------------
The platform ran on one crop at a time. `--crop sorghum` set the growing-degree
base and the heat-stress threshold for the whole farm, and every field in the
report was labelled sorghum whatever was standing in it. That is wrong in the
ordinary case rather than the edge case: a Gezira tenancy rotates cotton,
sorghum, wheat and groundnut, and a wheat block inside a sorghum run was given
sorghum's 38 degC heat threshold - six degrees above the temperature at which
wheat actually starts losing grain. The number was not missing. It was wrong,
and nothing on the screen said so.

So a crop is now an object with parameters, every field carries its own, and
the engine reads the field's crop rather than the run's.

WHERE THE NUMBERS COME FROM, AND WHAT THAT MEANS
------------------------------------------------
The Kc values are FAO-56 Table 12. The base temperatures and heat thresholds
are the conventional published figures. NONE of them is a Sudanese trial
result, and several are known to be regionally sensitive - a heat threshold in
particular is a variety property as much as a species one, and Gezira varieties
have been selected under heat for a century.

Every parameter therefore carries `basis`, and the engine reports the crop it
used. A wrong crop is now visible; before, it was invisible.

WHY Kc IS HERE AT ALL WHEN Kcb COMES FROM NDVI
----------------------------------------------
The water calculation derives Kcb from observed canopy greenness precisely so
it does not depend on a table indexed by a growth stage nobody measured. The
tabulated Kc stays here as a CHECK: a Kcb from NDVI that sits far outside the
tabulated range for the declared crop means either the crop label is wrong or
the canopy is not what the label says, and both are worth knowing. It is not
used to compute the requirement.
"""

from __future__ import annotations

from typing import Optional


# ==============================================================================
# THE CROPS
# ==============================================================================
#
# season_days: typical length of the growing season under Sudanese irrigation,
#   used only for a sanity range, never to fill in a missing measurement.
# sowing_months: the window in which the crop normally goes in, for the same
#   purpose - flagging a declared crop that cannot be what is standing there.

CROPS = {
    "sorghum": {
        "ar": "ذرة رفيعة", "en": "sorghum",
        "gdd_base_c": 10.0, "heat_stress_c": 38.0,
        "kc_ini": 0.30, "kc_mid": 1.05, "kc_end": 0.55,
        "root_depth_m": 1.5, "season_days": 130, "sowing_months": [6, 7, 8],
        "problems": ["sorghum_anthracnose", "sorghum_grain_mould",
                     "sorghum_downy_mildew", "sorghum_covered_smut",
                     "striga", "sorghum_stem_borer"],
    },
    "wheat": {
        "ar": "قمح", "en": "wheat",
        "gdd_base_c": 4.0, "heat_stress_c": 32.0,
        "kc_ini": 0.30, "kc_mid": 1.15, "kc_end": 0.30,
        "root_depth_m": 1.5, "season_days": 105, "sowing_months": [11, 12],
        "problems": ["wheat_stem_rust", "wheat_leaf_rust", "wheat_yellow_rust",
                     "powdery_mildew", "wheat_loose_smut", "aphids"],
    },
    "cotton": {
        "ar": "قطن", "en": "cotton",
        "gdd_base_c": 15.5, "heat_stress_c": 35.0,
        "kc_ini": 0.35, "kc_mid": 1.18, "kc_end": 0.60,
        "root_depth_m": 1.4, "season_days": 190, "sowing_months": [7, 8],
        "problems": ["cotton_bacterial_blight", "cotton_leaf_curl",
                     "fusarium_wilt", "whitefly", "bollworm"],
    },
    "groundnut": {
        "ar": "فول سوداني", "en": "groundnut",
        "gdd_base_c": 10.0, "heat_stress_c": 34.0,
        "kc_ini": 0.40, "kc_mid": 1.15, "kc_end": 0.60,
        "root_depth_m": 0.7, "season_days": 135, "sowing_months": [6, 7],
        "problems": ["groundnut_leaf_spot", "groundnut_rosette",
                     "groundnut_rust"],
    },
    "sesame": {
        "ar": "سمسم", "en": "sesame",
        "gdd_base_c": 12.0, "heat_stress_c": 40.0,
        "kc_ini": 0.35, "kc_mid": 1.10, "kc_end": 0.25,
        "root_depth_m": 1.0, "season_days": 100, "sowing_months": [6, 7],
        "problems": ["sesame_phyllody", "charcoal_rot"],
    },
    "maize": {
        "ar": "ذرة شامية", "en": "maize",
        "gdd_base_c": 10.0, "heat_stress_c": 35.0,
        "kc_ini": 0.30, "kc_mid": 1.20, "kc_end": 0.50,
        "root_depth_m": 1.2, "season_days": 125, "sowing_months": [6, 7, 11],
        "problems": ["maize_downy_mildew", "maize_stalk_rot",
                     "fall_armyworm"],
    },
    "sunflower": {
        "ar": "عبّاد الشمس", "en": "sunflower",
        "gdd_base_c": 8.0, "heat_stress_c": 34.0,
        "kc_ini": 0.35, "kc_mid": 1.10, "kc_end": 0.35,
        "root_depth_m": 1.2, "season_days": 125, "sowing_months": [7, 11, 12],
        "problems": ["sunflower_downy_mildew", "charcoal_rot"],
    },
    "onion": {
        "ar": "بصل", "en": "onion",
        "gdd_base_c": 6.0, "heat_stress_c": 30.0,
        "kc_ini": 0.70, "kc_mid": 1.05, "kc_end": 0.75,
        "root_depth_m": 0.4, "season_days": 150, "sowing_months": [10, 11],
        "problems": ["onion_purple_blotch", "onion_downy_mildew", "thrips"],
    },
    "faba_bean": {
        "ar": "فول مصري", "en": "faba bean",
        "gdd_base_c": 3.0, "heat_stress_c": 30.0,
        "kc_ini": 0.50, "kc_mid": 1.15, "kc_end": 0.30,
        "root_depth_m": 0.7, "season_days": 130, "sowing_months": [10, 11],
        "problems": ["chocolate_spot", "faba_rust", "aphids"],
    },
    "alfalfa": {
        "ar": "برسيم حجازي", "en": "alfalfa",
        "gdd_base_c": 5.0, "heat_stress_c": 35.0,
        "kc_ini": 0.40, "kc_mid": 0.95, "kc_end": 0.90,
        "root_depth_m": 1.5, "season_days": 365, "sowing_months": [10, 11],
        "problems": ["alfalfa_leaf_spot", "aphids"],
    },
    "tomato": {
        "ar": "طماطم", "en": "tomato",
        "gdd_base_c": 10.0, "heat_stress_c": 32.0,
        "kc_ini": 0.60, "kc_mid": 1.15, "kc_end": 0.80,
        "root_depth_m": 1.0, "season_days": 135, "sowing_months": [9, 10, 11],
        "problems": ["tomato_early_blight", "tomato_late_blight", "tylcv",
                     "whitefly"],
    },
    # The fallback. Its parameters are the middle of the table, and any figure
    # computed from it is marked as resting on a crop nobody declared - which
    # is a different statement from a figure computed for a known crop.
    "default": {
        "ar": "غير محدّد", "en": "unspecified",
        "gdd_base_c": 10.0, "heat_stress_c": 35.0,
        "kc_ini": 0.40, "kc_mid": 1.05, "kc_end": 0.50,
        "root_depth_m": 1.0, "season_days": 130, "sowing_months": [],
        "problems": [],
    },
}

BASIS = ("FAO-56 Table 12 for Kc; conventional published figures for base "
         "temperature and heat threshold. NOT Sudanese trial data. A heat "
         "threshold in particular is as much a variety property as a species "
         "one, and Gezira varieties have been selected under heat for a "
         "century.")

# Accepted spellings, so a field file written by a person rather than a program
# still resolves. Kept deliberately short: a fuzzy match that guesses would
# silently relabel a field, which is the failure this module was written to end.
ALIASES = {
    "ذرة": "sorghum", "ذرة رفيعة": "sorghum", "درة": "sorghum",
    "قمح": "wheat",
    "قطن": "cotton",
    "فول سوداني": "groundnut", "فول": "groundnut",
    "سمسم": "sesame",
    "ذرة شامية": "maize", "شامية": "maize",
    "عباد الشمس": "sunflower", "عبّاد الشمس": "sunflower",
    "بصل": "onion",
    "فول مصري": "faba_bean",
    "برسيم": "alfalfa", "برسيم حجازي": "alfalfa",
    "طماطم": "tomato",
    "faba": "faba_bean", "fava bean": "faba_bean", "broad bean": "faba_bean",
    "peanut": "groundnut", "lucerne": "alfalfa", "corn": "maize",
}


def resolve(name) -> str:
    """
    The canonical key for a crop name, or "default".

    Unknown names resolve to "default" rather than raising, because a report
    must still be produced for a field whose crop nobody recognised - but the
    caller can tell the difference with `is_known`, and the app says so.
    """
    if not name:
        return "default"
    key = str(name).strip().lower()
    if key in CROPS:
        return key
    if key in ALIASES:
        return ALIASES[key]
    return "default"


def is_known(name) -> bool:
    """True when the name resolved to a real crop rather than the fallback."""
    return bool(name) and resolve(name) != "default"


def get(name) -> dict:
    """The parameter block for a crop, with its resolved key attached."""
    key = resolve(name)
    declared = str(name).strip() if name else None
    # "recognised" separates a crop nobody declared from a crop that WAS
    # declared and was not understood. The first is a gap; the second is a
    # mistake somewhere, and the app should not present them alike.
    recognised = key != "default" or (declared or "").lower() == "default"
    return {"key": key, **CROPS[key], "basis": BASIS,
            "declared": declared, "recognised": recognised}


def label(name, ar: bool = False) -> str:
    """Display name for a crop, in the reader's language."""
    c = CROPS[resolve(name)]
    return c["ar"] if ar else c["en"]


def names(ar: bool = False) -> list:
    """Every crop, for a menu. `default` is offered last, since choosing it is
    a decision to say nothing rather than the natural first option."""
    keys = [k for k in CROPS if k != "default"] + ["default"]
    return [(k, label(k, ar)) for k in keys]


def gdd_base_c(name) -> float:
    return CROPS[resolve(name)]["gdd_base_c"]


def heat_stress_c(name) -> float:
    return CROPS[resolve(name)]["heat_stress_c"]


def problems(name) -> list:
    """Keys of the diseases, pests and parasitic weeds recorded for this crop.
    An empty list means nothing is registered - NOT that the crop has no
    problems, which is a claim no dataset here can support."""
    return list(CROPS[resolve(name)]["problems"])


# ==============================================================================
# CHECKS AGAINST THE DECLARED CROP
# ==============================================================================

def kcb_plausible(kcb: Optional[float], name, tolerance: float = 0.25) -> dict:
    """
    Does an NDVI-derived Kcb sit anywhere near the table for this crop?

    The water calculation does not use the table - it derives Kcb from observed
    greenness on purpose. This asks a different question: if the canopy implies
    a coefficient far outside the published range for the crop the field claims
    to be growing, then either the label is wrong or the canopy is not what the
    label says. Both are worth knowing, and neither is a reason to change the
    number.
    """
    if kcb is None:
        return {"status": "NOT AVAILABLE", "reason": "no Kcb computed"}
    c = CROPS[resolve(name)]
    lo, hi = c["kc_ini"] - tolerance, c["kc_mid"] + tolerance
    inside = lo <= kcb <= hi
    return {
        "status": "OK", "plausible": inside,
        "kcb": round(float(kcb), 3),
        "expected_range": [round(lo, 2), round(hi, 2)],
        "crop": resolve(name),
        "note": ("" if inside else
                 "the canopy implies a crop coefficient outside the published "
                 "range for this crop: either the crop label is wrong, or the "
                 "field is not carrying the canopy the label implies"),
        "note_ar": ("" if inside else
                    "الغطاء يشير إلى معامل محصول خارج المدى المنشور لهذا "
                    "المحصول: إمّا أنّ اسم المحصول خطأ، أو أنّ الحقل لا يحمل "
                    "الغطاء الذي يقتضيه الاسم"),
        "basis": "ARBITRARY tolerance of %.2f around FAO-56 Table 12" % tolerance,
    }


def season_plausible(name, sowing_month: Optional[int]) -> dict:
    """Is the declared sowing month inside the normal window for this crop?

    A wheat field sown in July in Gezira is a data-entry error, not a heroic
    agronomic experiment, and catching it before the run is cheaper than
    explaining the report afterwards.
    """
    c = CROPS[resolve(name)]
    months = c["sowing_months"]
    if not months or not sowing_month:
        return {"status": "NOT AVAILABLE",
                "reason": "no sowing month declared, or no window recorded "
                          "for this crop"}
    inside = int(sowing_month) in months
    return {"status": "OK", "plausible": inside, "months": months,
            "declared_month": int(sowing_month),
            "note": ("" if inside else
                     f"{c['en']} is normally sown in months {months} in this "
                     "region; check the date before reading the season figures"),
            "note_ar": ("" if inside else
                        f"{c['ar']} يُزرع عادةً في الأشهر {months} في هذه "
                        "المنطقة؛ راجع التاريخ قبل قراءة أرقام الموسم")}
