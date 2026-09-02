"""
Disease and pest: three rungs, and a refusal at the top.

THE CLAIM THIS MODULE WILL NOT MAKE
-----------------------------------
A satellite cannot name a disease. This is not a limitation of the current
implementation, to be lifted by better code or a bigger model - it is a
property of the measurement. Sentinel-2 sees reflectance in a handful of broad
bands. Anthracnose, water stress, nitrogen deficiency, salinity, stem borer,
lodging and a badly set seed drill all lower NDVI and raise canopy temperature,
and they do it in ways those bands cannot separate. Hyperspectral instruments
can sometimes separate SOME pathogens under controlled conditions; ten-metre
multispectral over a Gezira tenancy cannot.

Products in this market do claim it. A field is drawn red and captioned with a
pathogen. That is a guess wearing the clothes of a measurement, and the cost of
it is not abstract: a farmer sprays a fungicide against a disease they do not
have, spends money they do not have, and learns that the tool lies. The second
consequence is worse than the first, because it also destroys the value of the
readings that WERE real.

So this module is built as a ladder, and each rung says what it is:

  RUNG 1  ANOMALY   satellite. "Part of this field is unlike the rest of it."
                    Non-specific by construction. It gives a place to walk to
                    and a size, and it names nothing.

  RUNG 2  RISK      weather. "Conditions in the last N days were favourable to
                    infection by X." This is about the AIR, not the field. It
                    is true of every field under that sky, healthy ones
                    included, and it is a reason to go and look.

  RUNG 3  REPORTED  a human. "A scout saw X on this date." This is the only
                    rung that names a disease as present, and it is the only
                    one that can.

The ladder mirrors the nutrition ladder already in this platform (relative ->
sufficiency -> calibrated) for the same reason: a refusal that cannot say what
would lift it teaches people to stop trying.

WHAT THE WEATHER MODELS ARE, HONESTLY
-------------------------------------
Infection-favourability windows for the temperature and wetness ranges each
pathogen needs. They come from published phytopathology, mostly from other
countries, and NONE has been validated against Sudanese disease surveys. They
answer "was the weather right for this" and not "did this happen". Every one
carries its basis, and the band names say FAVOURABLE, never PRESENT.

Leaf wetness is the variable these models really want and no satellite measures
it. The proxy here is a rain day OR a daily maximum relative humidity above the
model's threshold - the standard daily surrogate for dew formation, computed
from ERA5-Land dewpoint against the day's minimum temperature. It is a proxy,
it is named as one, and it will be wrong on a windy night.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import crops as C


# ==============================================================================
# THE REGISTRY
# ==============================================================================
#
# weather_model is None where no defensible daily model exists. That is not an
# oversight to be filled in later with something plausible: a vector-borne
# virus is driven by insect population dynamics, and a soil-borne wilt by
# inoculum that has been in that soil for years. Inventing a temperature window
# for those would produce a number every day and mean nothing. Those entries
# reach the screen only through scouting, and say so.

PROBLEMS = {
    # ---------------------------------------------------------------- sorghum
    "sorghum_anthracnose": {
        "ar": "أنثراكنوز الذرة", "en": "sorghum anthracnose",
        "kind": "disease",
        "weather_model": {"t_min_c": 25.0, "t_max_c": 30.0, "rh_max_pct": 90.0,
                          "rain_mm": 1.0, "days_needed": 4, "window_days": 14},
        "basis": "Warm and wet favours Colletotrichum sublineolum; published "
                 "ranges, not validated in Sudan.",
        "scout_ar": "بقع بيضاوية على الأوراق مركزها فاتح وحافّتها أرجوانية، "
                    "وتعفّن أحمر في الساق عند الشقّ الطولي.",
        "scout_en": "Oval leaf spots with a pale centre and purple margin; "
                    "red rot inside the stalk when split lengthwise.",
    },
    "sorghum_grain_mould": {
        "ar": "عفن حبوب الذرة", "en": "sorghum grain mould",
        "kind": "disease",
        "weather_model": {"t_min_c": 22.0, "t_max_c": 32.0, "rh_max_pct": 85.0,
                          "rain_mm": 2.0, "days_needed": 5, "window_days": 21},
        "basis": "Rain and humidity during flowering and grain fill. The "
                 "window here is calendar-based; it does not know the crop is "
                 "at that stage - read it with the green-up date.",
        "scout_ar": "حبوب متلوّنة بالوردي أو الأسود أو الأبيض في الرأس، "
                    "وخفّة وزن الحبّة.",
        "scout_en": "Pink, black or white discoloured grain in the head; "
                    "light, chaffy kernels.",
    },
    "sorghum_downy_mildew": {
        "ar": "البياض الزغبي في الذرة", "en": "sorghum downy mildew",
        "kind": "disease",
        "weather_model": {"t_min_c": 20.0, "t_max_c": 28.0, "rh_max_pct": 90.0,
                          "rain_mm": 3.0, "days_needed": 3, "window_days": 21},
        "basis": "Wet soil at emergence favours systemic infection; the risk "
                 "window is early season and this model does not know that.",
        "scout_ar": "خطوط صفراء طولية على الأوراق مع زغب أبيض على السطح "
                    "السفلي في الصباح الباكر، ثمّ تشقّق الورقة إلى شرائط.",
        "scout_en": "Yellow streaks with white down on the leaf underside "
                    "early in the morning; leaves later shred into strips.",
    },
    "sorghum_covered_smut": {
        "ar": "التفحّم المغطّى", "en": "covered kernel smut",
        "kind": "disease", "weather_model": None,
        "basis": "Seed-borne. Driven by seed treatment and seed source, not by "
                 "this season's weather - so no weather model is offered.",
        "scout_ar": "حبوب مستبدلة بأكياس رمادية مملوءة بمسحوق أسود.",
        "scout_en": "Kernels replaced by grey sacs full of black powder.",
    },
    "striga": {
        "ar": "البودة (الستريقا)", "en": "striga (witchweed)",
        "kind": "parasitic weed", "weather_model": None,
        "basis": "A parasitic plant, not a pathogen. Its distribution is set by "
                 "a seed bank that persists in the soil for years and by soil "
                 "fertility, neither of which a fortnight of weather predicts.",
        "scout_ar": "نبات صغير بأزهار أرجوانية بين صفوف الذرة، وتقزّم المحصول "
                    "حوله رغم الريّ.",
        "scout_en": "A small purple-flowered plant between the sorghum rows, "
                    "with stunted crop around it despite irrigation.",
    },
    "sorghum_stem_borer": {
        "ar": "ثاقبة ساق الذرة", "en": "sorghum stem borer",
        "kind": "pest", "weather_model": None,
        "basis": "Insect population dynamics, not an infection window.",
        "scout_ar": "ثقوب في الساق ونشارة، و«قلب ميت» في القمة النامية.",
        "scout_en": "Boreholes and frass in the stem; a dead heart at the "
                    "growing point.",
    },
    # ------------------------------------------------------------------ wheat
    "wheat_stem_rust": {
        "ar": "الصدأ الأسود في القمح", "en": "wheat stem rust",
        "kind": "disease",
        "weather_model": {"t_min_c": 18.0, "t_max_c": 30.0, "rh_max_pct": 90.0,
                          "rain_mm": 0.2, "days_needed": 3, "window_days": 14},
        "basis": "Puccinia graminis needs several hours of leaf wetness at "
                 "warm temperatures. Sudanese wheat is a winter crop under "
                 "heavy dew, so this fires on dew, not only on rain.",
        "scout_ar": "بثرات بنّية محمرّة بارزة على الساق والغمد، تترك مسحوقًا "
                    "على اليد.",
        "scout_en": "Raised red-brown pustules on stems and sheaths that leave "
                    "powder on the hand.",
    },
    "wheat_leaf_rust": {
        "ar": "الصدأ البنّي في القمح", "en": "wheat leaf rust",
        "kind": "disease",
        "weather_model": {"t_min_c": 15.0, "t_max_c": 25.0, "rh_max_pct": 90.0,
                          "rain_mm": 0.2, "days_needed": 3, "window_days": 14},
        "basis": "Puccinia triticina; cooler window than stem rust.",
        "scout_ar": "بثرات برتقالية بنّية صغيرة مبعثرة على سطح الورقة العلوي.",
        "scout_en": "Small orange-brown pustules scattered on the upper leaf "
                    "surface.",
    },
    "wheat_yellow_rust": {
        "ar": "الصدأ الأصفر في القمح", "en": "wheat yellow rust",
        "kind": "disease",
        "weather_model": {"t_min_c": 8.0, "t_max_c": 18.0, "rh_max_pct": 90.0,
                          "rain_mm": 0.2, "days_needed": 3, "window_days": 14},
        "basis": "Puccinia striiformis is the cool-weather rust. In most of "
                 "Gezira this window rarely opens - which is itself worth "
                 "seeing, rather than being silently absent.",
        "scout_ar": "بثرات صفراء في خطوط منتظمة موازية لعروق الورقة.",
        "scout_en": "Yellow pustules in neat stripes along the leaf veins.",
    },
    "powdery_mildew": {
        "ar": "البياض الدقيقي", "en": "powdery mildew",
        "kind": "disease",
        "weather_model": {"t_min_c": 15.0, "t_max_c": 22.0, "rh_max_pct": 80.0,
                          "rain_mm": None, "days_needed": 4, "window_days": 14},
        "basis": "Unlike the rusts, powdery mildew wants humidity WITHOUT free "
                 "water, so rain is not part of this model.",
        "scout_ar": "بقع بيضاء دقيقية على الأوراق تُمسح باليد.",
        "scout_en": "White floury patches on leaves that rub off.",
    },
    "wheat_loose_smut": {
        "ar": "التفحّم السائب", "en": "loose smut",
        "kind": "disease", "weather_model": None,
        "basis": "Seed-borne and set at the previous flowering; this season's "
                 "weather does not drive it.",
        "scout_ar": "سنابل تحوّلت إلى مسحوق أسود ينثره الهواء.",
        "scout_en": "Heads turned to black powder that blows away.",
    },
    "aphids": {
        "ar": "المنّ", "en": "aphids", "kind": "pest", "weather_model": None,
        "basis": "Population dynamics and natural enemies, not an infection "
                 "window.",
        "scout_ar": "تجمّعات حشرات صغيرة على السنابل وأسفل الأوراق، وندوة "
                    "عسلية لزجة.",
        "scout_en": "Colonies on heads and leaf undersides; sticky honeydew.",
    },
    # ----------------------------------------------------------------- cotton
    "cotton_bacterial_blight": {
        "ar": "اللفحة البكتيرية في القطن", "en": "cotton bacterial blight",
        "kind": "disease",
        "weather_model": {"t_min_c": 25.0, "t_max_c": 35.0, "rh_max_pct": 85.0,
                          "rain_mm": 5.0, "days_needed": 2, "window_days": 14},
        "basis": "Xanthomonas spreads with driving rain; heavy-rain days carry "
                 "more weight than humid ones, so the rain threshold is high.",
        "scout_ar": "بقع زاويّة مشبعة بالماء على الأوراق تحدّها العروق، "
                    "وتصبح بنّية.",
        "scout_en": "Angular water-soaked leaf spots bounded by the veins, "
                    "turning brown.",
    },
    "cotton_leaf_curl": {
        "ar": "تجعّد أوراق القطن", "en": "cotton leaf curl virus",
        "kind": "disease", "weather_model": None,
        "basis": "A virus carried by whitefly. Its risk is a whitefly count, "
                 "not a temperature window - see the whitefly entry, and scout "
                 "for both together.",
        "scout_ar": "تجعّد الأوراق إلى أعلى، وتعرّق سميك، ونموّات على السطح "
                    "السفلي.",
        "scout_en": "Upward leaf curling, thickened veins, and enations on the "
                    "underside.",
    },
    "fusarium_wilt": {
        "ar": "الذبول الفيوزارمي", "en": "fusarium wilt",
        "kind": "disease", "weather_model": None,
        "basis": "Soil-borne. The inoculum has been in that soil for years; a "
                 "fortnight of weather says nothing about it.",
        "scout_ar": "ذبول نصف النبات، واسمرار الأوعية عند شقّ الساق.",
        "scout_en": "One-sided wilting; browned vascular tissue when the stem "
                    "is split.",
    },
    "whitefly": {
        "ar": "الذبابة البيضاء", "en": "whitefly", "kind": "pest",
        "weather_model": None,
        "basis": "Population dynamics. Hot dry weather favours build-up, but "
                 "no daily threshold here would be defensible.",
        "scout_ar": "سحابة من حشرات بيضاء دقيقة عند هزّ النبات، وندوة عسلية "
                    "وعفن أسود.",
        "scout_en": "A cloud of tiny white insects when the plant is shaken; "
                    "honeydew and sooty mould.",
    },
    "bollworm": {
        "ar": "دودة اللوز", "en": "bollworm", "kind": "pest",
        "weather_model": None, "basis": "Population dynamics.",
        "scout_ar": "ثقوب في اللوز وتساقطه، ويرقات داخل اللوزة.",
        "scout_en": "Holes in bolls and boll shedding; larvae inside.",
    },
    # -------------------------------------------------------------- groundnut
    "groundnut_leaf_spot": {
        "ar": "تبقّع أوراق الفول السوداني", "en": "groundnut leaf spot",
        "kind": "disease",
        "weather_model": {"t_min_c": 22.0, "t_max_c": 30.0, "rh_max_pct": 90.0,
                          "rain_mm": 1.0, "days_needed": 4, "window_days": 14},
        "basis": "Cercospora needs prolonged leaf wetness at warm "
                 "temperatures.",
        "scout_ar": "بقع دائرية بنّية إلى سوداء على الأوراق، وهالة صفراء "
                    "حولها، ثمّ تساقط الأوراق.",
        "scout_en": "Round brown to black leaf spots with a yellow halo, "
                    "followed by defoliation.",
    },
    "groundnut_rosette": {
        "ar": "تقزّم الفول السوداني", "en": "groundnut rosette",
        "kind": "disease", "weather_model": None,
        "basis": "Virus complex carried by aphids; a vector problem, not an "
                 "infection window.",
        "scout_ar": "تقزّم شديد واصفرار، وأوراق صغيرة متجمّعة.",
        "scout_en": "Severe stunting and yellowing with small bunched leaves.",
    },
    "groundnut_rust": {
        "ar": "صدأ الفول السوداني", "en": "groundnut rust",
        "kind": "disease",
        "weather_model": {"t_min_c": 20.0, "t_max_c": 28.0, "rh_max_pct": 90.0,
                          "rain_mm": 0.5, "days_needed": 3, "window_days": 14},
        "basis": "Puccinia arachidis; humid warm nights.",
        "scout_ar": "بثرات برتقالية على السطح السفلي للورقة.",
        "scout_en": "Orange pustules on the leaf underside.",
    },
    # ----------------------------------------------------------------- sesame
    "sesame_phyllody": {
        "ar": "الفيلودي في السمسم", "en": "sesame phyllody",
        "kind": "disease", "weather_model": None,
        "basis": "Phytoplasma carried by leafhoppers. A vector problem, and "
                 "one of the most damaging in Sudanese sesame - which is a "
                 "reason to scout for it, not a reason to invent a model.",
        "scout_ar": "تحوّل الأزهار إلى أوراق خضراء، وتفرّع شاذّ، وعقم القرون.",
        "scout_en": "Flowers turned into green leafy structures, abnormal "
                    "branching, sterile capsules.",
    },
    "charcoal_rot": {
        "ar": "العفن الفحمي", "en": "charcoal rot", "kind": "disease",
        "weather_model": {"t_min_c": 30.0, "t_max_c": 45.0, "rh_max_pct": None,
                          "rain_mm": None, "days_needed": 7, "window_days": 21,
                          "dry": True},
        "basis": "Macrophomina is the exception among these: it is favoured by "
                 "HEAT AND DROUGHT, not wetness, so its model counts hot days "
                 "without rain.",
        "scout_ar": "جفاف مفاجئ للنبات، ولحاء يتقشّر عن أنسجة رمادية منقّطة "
                    "بالأسود.",
        "scout_en": "Sudden plant death; bark peels to reveal grey tissue "
                    "peppered with black specks.",
    },
    # ------------------------------------------------------------------ maize
    "maize_downy_mildew": {
        "ar": "البياض الزغبي في الذرة الشامية", "en": "maize downy mildew",
        "kind": "disease",
        "weather_model": {"t_min_c": 20.0, "t_max_c": 28.0, "rh_max_pct": 90.0,
                          "rain_mm": 3.0, "days_needed": 3, "window_days": 21},
        "basis": "Wet soil and high humidity at early growth.",
        "scout_ar": "خطوط صفراء طولية وزغب أبيض على السطح السفلي.",
        "scout_en": "Yellow streaks and white down on the leaf underside.",
    },
    "maize_stalk_rot": {
        "ar": "عفن ساق الذرة الشامية", "en": "maize stalk rot",
        "kind": "disease", "weather_model": None,
        "basis": "Several pathogens with different drivers; a single window "
                 "would be a false summary of them.",
        "scout_ar": "ساق طرية عند الضغط، وسقوط النبات قبل الحصاد.",
        "scout_en": "Stalks soft when squeezed; lodging before harvest.",
    },
    "fall_armyworm": {
        "ar": "دودة الحشد الخريفية", "en": "fall armyworm", "kind": "pest",
        "weather_model": None,
        "basis": "A migratory pest, present in Sudan since 2017. Its arrival "
                 "is driven by migration, not by local weather.",
        "scout_ar": "نشارة رطبة في قلب النبات، وثقوب غير منتظمة في الأوراق، "
                    "ويرقة ذات أربع نقاط سوداء على الحلقة قبل الأخيرة.",
        "scout_en": "Moist frass in the whorl, ragged leaf holes, and a larva "
                    "with four black dots on the second-to-last segment.",
    },
    # ------------------------------------------------------------------ onion
    "onion_purple_blotch": {
        "ar": "اللطعة الأرجوانية في البصل", "en": "onion purple blotch",
        "kind": "disease",
        "weather_model": {"t_min_c": 21.0, "t_max_c": 30.0, "rh_max_pct": 90.0,
                          "rain_mm": 0.2, "days_needed": 3, "window_days": 14},
        "basis": "Alternaria porri; warm humid nights with dew.",
        "scout_ar": "بقع بيضاوية غائرة بمركز أرجواني على الأوراق ورؤوس البذور.",
        "scout_en": "Sunken oval lesions with purple centres on leaves and "
                    "seed stalks.",
    },
    "onion_downy_mildew": {
        "ar": "البياض الزغبي في البصل", "en": "onion downy mildew",
        "kind": "disease",
        "weather_model": {"t_min_c": 10.0, "t_max_c": 22.0, "rh_max_pct": 95.0,
                          "rain_mm": 0.2, "days_needed": 3, "window_days": 14},
        "basis": "Peronospora destructor; cool and very humid.",
        "scout_ar": "زغب رمادي بنفسجي على الأوراق في الصباح، ثمّ انهيارها.",
        "scout_en": "Grey-violet down on leaves in the morning; leaves then "
                    "collapse.",
    },
    "thrips": {
        "ar": "التربس", "en": "thrips", "kind": "pest", "weather_model": None,
        "basis": "Population dynamics; hot dry weather favours build-up.",
        "scout_ar": "خدوش فضّية على الأوراق وحشرات دقيقة في قواعدها.",
        "scout_en": "Silvery scarring on leaves; tiny insects at the leaf "
                    "bases.",
    },
    # -------------------------------------------------------------- faba bean
    "chocolate_spot": {
        "ar": "التبقّع البنّي في الفول", "en": "chocolate spot",
        "kind": "disease",
        "weather_model": {"t_min_c": 15.0, "t_max_c": 25.0, "rh_max_pct": 90.0,
                          "rain_mm": 0.5, "days_needed": 4, "window_days": 14},
        "basis": "Botrytis fabae; cool humid conditions in a dense canopy.",
        "scout_ar": "بقع بنّية محمرّة صغيرة تتّحد فتسودّ الورقة كلّها.",
        "scout_en": "Small red-brown spots that coalesce until the leaf "
                    "blackens.",
    },
    "faba_rust": {
        "ar": "صدأ الفول", "en": "faba bean rust", "kind": "disease",
        "weather_model": {"t_min_c": 18.0, "t_max_c": 26.0, "rh_max_pct": 90.0,
                          "rain_mm": 0.2, "days_needed": 3, "window_days": 14},
        "basis": "Uromyces viciae-fabae.",
        "scout_ar": "بثرات بنّية صغيرة بهالة فاتحة على الأوراق.",
        "scout_en": "Small brown pustules with a pale halo.",
    },
    # -------------------------------------------------------------- vegetable
    "tomato_early_blight": {
        "ar": "اللفحة المبكّرة في الطماطم", "en": "tomato early blight",
        "kind": "disease",
        "weather_model": {"t_min_c": 24.0, "t_max_c": 30.0, "rh_max_pct": 90.0,
                          "rain_mm": 0.5, "days_needed": 3, "window_days": 14},
        "basis": "Alternaria solani; warm with leaf wetness.",
        "scout_ar": "بقع بنّية بحلقات متّحدة المركز على الأوراق السفلى.",
        "scout_en": "Brown spots with concentric rings on the lower leaves.",
    },
    "tomato_late_blight": {
        "ar": "اللفحة المتأخّرة في الطماطم", "en": "tomato late blight",
        "kind": "disease",
        "weather_model": {"t_min_c": 10.0, "t_max_c": 22.0, "rh_max_pct": 95.0,
                          "rain_mm": 1.0, "days_needed": 2, "window_days": 10},
        "basis": "Phytophthora infestans; cool and very wet. It moves in days, "
                 "so the window is short.",
        "scout_ar": "بقع زيتية داكنة على الأوراق والسيقان، وزغب أبيض على "
                    "الحافّة في الرطوبة.",
        "scout_en": "Dark greasy lesions on leaves and stems, with white down "
                    "at the margin in humid air.",
    },
    "tylcv": {
        "ar": "تجعّد واصفرار أوراق الطماطم", "en": "tomato yellow leaf curl",
        "kind": "disease", "weather_model": None,
        "basis": "Whitefly-transmitted virus; a vector problem.",
        "scout_ar": "تقزّم شديد، وأوراق صغيرة صفراء الحواف ملتفّة لأعلى.",
        "scout_en": "Severe stunting with small leaves, yellow margins, curled "
                    "upward.",
    },
    "alfalfa_leaf_spot": {
        "ar": "تبقّع أوراق البرسيم", "en": "alfalfa leaf spot",
        "kind": "disease",
        "weather_model": {"t_min_c": 15.0, "t_max_c": 25.0, "rh_max_pct": 90.0,
                          "rain_mm": 0.5, "days_needed": 4, "window_days": 14},
        "basis": "Several leaf-spotting fungi with broadly similar windows.",
        "scout_ar": "بقع بنّية صغيرة على الوريقات وتساقطها.",
        "scout_en": "Small brown spots on leaflets, followed by leaf drop.",
    },
    "sunflower_downy_mildew": {
        "ar": "البياض الزغبي في عبّاد الشمس", "en": "sunflower downy mildew",
        "kind": "disease",
        "weather_model": {"t_min_c": 15.0, "t_max_c": 25.0, "rh_max_pct": 90.0,
                          "rain_mm": 3.0, "days_needed": 3, "window_days": 21},
        "basis": "Plasmopara halstedii; saturated soil at emergence.",
        "scout_ar": "اصفرار حول العروق وتقزّم، وزغب أبيض أسفل الورقة.",
        "scout_en": "Vein-bounded yellowing and stunting, white down beneath.",
    },
}

RISK_BANDS = ("NOT FAVOURABLE", "MARGINAL", "FAVOURABLE")


def li(name: str) -> str:
    """
    Attach the Arabic preposition "li-" to a name correctly.

    Arabic prefixes join the following word: li + عفن is لعفن, written as one
    word. The tatweel form لـ exists for the case where the following text
    CANNOT join - Latin script, a numeral, a dataset name - as in لـSentinel-2.

    The first live run printed "لـعفن حبوب الذرة", which is the tatweel form
    in front of an Arabic word: it renders as a stranded connector and reads,
    to an Arabic speaker, the way "t he grain mould" reads in English. Small,
    and this application's whole first language.
    """
    if not name:
        return "لـ"
    return ("ل" if "؀" <= name[0] <= "ۿ" else "لـ") + name


def label(key: str, ar: bool = False) -> str:
    p = PROBLEMS.get(key)
    if not p:
        return key
    return p["ar"] if ar else p["en"]


def for_crop(crop) -> list:
    """Registered problems for a crop, as (key, entry) pairs."""
    return [(k, PROBLEMS[k]) for k in C.problems(crop) if k in PROBLEMS]


# ==============================================================================
# HUMIDITY - the variable the models want and nothing measures directly
# ==============================================================================

def _es_kpa(t_c: float) -> float:
    """Saturation vapour pressure, FAO-56 eq. 11. Duplicated from agronomy so
    this module stays importable without an Earth Engine dependency chain; a
    test asserts the two agree."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def relative_humidity_pct(t_dew_c: Optional[float],
                          t_c: Optional[float]) -> Optional[float]:
    """
    RH from dewpoint against air temperature.

    Fed the day's MINIMUM temperature this gives the daily maximum RH, which is
    the standard daily surrogate for whether dew formed. It is a surrogate: a
    windy night can keep leaves dry at 95% RH, and this will not know.
    """
    if t_dew_c is None or t_c is None:
        return None
    rh = 100.0 * _es_kpa(float(t_dew_c)) / _es_kpa(float(t_c))
    # A dewpoint above air temperature is a data artefact, not supersaturation.
    return round(min(100.0, max(0.0, rh)), 1)


def favourable_day(t_min_c, t_max_c, t_dew_c, rain_mm, model: dict) -> bool:
    """
    Was one day favourable to infection under this model?

    Temperature is tested against the day's MEAN, because a pathogen does not
    experience the maximum all day. Wetness is satisfied by measurable rain OR
    by a daily maximum RH above the model's threshold. A model marked `dry`
    inverts the wetness test - charcoal rot wants heat and drought.
    """
    if t_min_c is None or t_max_c is None:
        return False
    t_mean = (float(t_min_c) + float(t_max_c)) / 2.0
    if not (model["t_min_c"] <= t_mean <= model["t_max_c"]):
        return False

    rain = float(rain_mm) if rain_mm is not None else None
    rh_max = relative_humidity_pct(t_dew_c, t_min_c)

    if model.get("dry"):
        # Favourable when it did NOT rain. Unknown rain is not dryness, so an
        # absent figure fails the test rather than passing it silently.
        return rain is not None and rain < 1.0

    wet = False
    if model.get("rain_mm") is not None and rain is not None:
        wet = wet or rain >= model["rain_mm"]
    if model.get("rh_max_pct") is not None and rh_max is not None:
        wet = wet or rh_max >= model["rh_max_pct"]
    if model.get("rain_mm") is None and model.get("rh_max_pct") is None:
        wet = True          # a temperature-only model
    return wet


def infection_risk(key: str, t_min: Sequence, t_max: Sequence,
                   t_dew: Sequence, rain: Sequence,
                   as_of_index: Optional[int] = None) -> dict:
    """
    Weather favourability for one problem over its own trailing window.

    Returns a band, the count of favourable days, and the sentence that must
    travel with it. The band is about the AIR: it is equally true of every
    healthy field under that sky, and it is a reason to walk out and look, not
    a finding about this field.
    """
    entry = PROBLEMS.get(key)
    if not entry:
        return {"status": "NOT AVAILABLE", "reason": f"unknown problem {key}"}
    model = entry.get("weather_model")
    if not model:
        return {"status": "NO MODEL", "problem": key,
                "kind": entry["kind"], "basis": entry["basis"],
                "reason": entry["basis"],
                "reason_ar": "لا نموذج طقس دفاعي عنه لهذه الآفة — تُرصد "
                             "بالكشف الميداني وحده."}

    n = min(len(t_min or []), len(t_max or []))
    if n == 0:
        return {"status": "NOT AVAILABLE", "problem": key,
                "reason": "no daily weather series for this field"}

    end = n if as_of_index is None else min(int(as_of_index) + 1, n)
    start = max(0, end - int(model["window_days"]))
    days = 0
    for i in range(start, end):
        if favourable_day(
                t_min[i] if i < len(t_min) else None,
                t_max[i] if i < len(t_max) else None,
                t_dew[i] if t_dew and i < len(t_dew) else None,
                rain[i] if rain and i < len(rain) else None, model):
            days += 1

    need = int(model["days_needed"])
    if days >= need:
        band = "FAVOURABLE"
    elif days >= max(1, need - 1):
        band = "MARGINAL"
    else:
        band = "NOT FAVOURABLE"

    return {
        "status": "OK", "problem": key, "kind": entry["kind"],
        "band": band, "favourable_days": days, "days_needed": need,
        "window_days": int(model["window_days"]),
        "window_covered_days": end - start,
        "basis": entry["basis"],
        "claim": ("Weather favourable to infection. NOT a detection: this is "
                  "true of every field under the same sky, healthy ones "
                  "included."),
        "claim_ar": ("طقس مواتٍ للإصابة. وليس كشفًا: هذا يصدق على كل حقل تحت "
                     "السماء نفسها، بما فيها السليمة."),
        "scout_for": entry["scout_en"], "scout_for_ar": entry["scout_ar"],
    }


def season_scan(key: str, t_min: Sequence, t_max: Sequence, t_dew: Sequence,
                rain: Sequence, start_date: Optional[str] = None) -> dict:
    """
    The worst window this problem had ALL SEASON, and when it opened.

    WHY THIS EXISTS SEPARATELY FROM infection_risk
    ----------------------------------------------
    `infection_risk` looks at the trailing window - the last fortnight - which
    is the right question while a crop is standing: is it favourable NOW,
    should somebody walk out this week.

    Run over a season that has already finished, that same question is nearly
    useless, and the first live run showed exactly why: the season window ends
    on 31 March, late March in Gezira is hot and dry, and every disease came
    back 0/14 days. All correct, and all uninformative - the answer described
    the dry season, not the crop.

    The retrospective question is different: did a favourable window open at
    any point while the crop was growing, and when. That is the same model
    slid across the season rather than read at its end. It says nothing about
    whether infection HAPPENED - see the refusal at the top of this module -
    but "conditions were favourable for six days in the fortnight ending 12
    September" is something a person can check against what they saw, and
    something to plan the next season's scouting around.
    """
    entry = PROBLEMS.get(key)
    if not entry or not entry.get("weather_model"):
        return {"status": "NO MODEL", "problem": key}
    model = entry["weather_model"]
    n = min(len(t_min or []), len(t_max or []))
    if n == 0:
        return {"status": "NOT AVAILABLE", "problem": key,
                "reason": "no daily weather series"}

    # Favourability per day, computed once, then summed over each window.
    fav = [favourable_day(
        t_min[i], t_max[i],
        t_dew[i] if t_dew and i < len(t_dew) else None,
        rain[i] if rain and i < len(rain) else None, model) for i in range(n)]

    width = int(model["window_days"])
    best_count, best_end = 0, None
    running = 0
    for i in range(n):
        running += 1 if fav[i] else 0
        if i >= width:
            running -= 1 if fav[i - width] else 0
        if running > best_count:
            best_count, best_end = running, i

    need = int(model["days_needed"])
    opened = best_count >= need
    out = {
        "status": "OK", "problem": key, "kind": entry["kind"],
        "opened": opened,
        "worst_window_days": best_count, "days_needed": need,
        "window_days": width, "total_favourable_days": sum(fav),
        "season_days": n,
        "basis": entry["basis"],
        "claim": ("Conditions favourable to infection occurred during the "
                  "season. NOT a finding that infection happened, and not "
                  "specific to this field - it describes the air over the "
                  "area."),
        "claim_ar": ("حدثت خلال الموسم ظروف مواتية للإصابة. وليس هذا كشفًا "
                     "بأنّ الإصابة وقعت، ولا هو خاصّ بهذا الحقل — بل يصف "
                     "الهواء فوق المنطقة."),
        "scout_for": entry["scout_en"], "scout_for_ar": entry["scout_ar"],
    }
    if best_end is not None and start_date:
        from datetime import datetime, timedelta
        try:
            d0 = datetime.strptime(start_date, "%Y-%m-%d")
            out["worst_window_end"] = (d0 + timedelta(days=best_end)).date(
                ).isoformat()
            out["worst_window_start"] = (
                d0 + timedelta(days=max(0, best_end - width + 1))).date(
                ).isoformat()
        except ValueError:
            pass
    return out


def crop_risk(crop, t_min, t_max, t_dew, rain,
              as_of_index: Optional[int] = None,
              start_date: Optional[str] = None) -> dict:
    """Every registered problem for a crop, ordered worst first.

    Problems with no weather model are returned too, in `no_model`, so the
    absence of a risk line for fall armyworm reads as "nothing here can predict
    it" rather than "it is fine"."""
    scored, no_model = [], []
    for key, _entry in for_crop(crop):
        r = infection_risk(key, t_min, t_max, t_dew, rain, as_of_index)
        if r.get("status") == "OK":
            scored.append(r)
        elif r.get("status") == "NO MODEL":
            no_model.append(r)
    # The season scan runs alongside the trailing window. On a report for a
    # season that has finished, the trailing window describes the dry season
    # and the scan describes the crop - and which of the two is useful depends
    # entirely on when the run happened, which the engine cannot know.
    scans = [season_scan(key, t_min, t_max, t_dew, rain, start_date)
             for key, _e in for_crop(crop)]
    scans = [x for x in scans if x.get("status") == "OK"]
    scans.sort(key=lambda x: -x.get("worst_window_days", 0))

    order = {b: i for i, b in enumerate(reversed(RISK_BANDS))}
    scored.sort(key=lambda r: (order.get(r["band"], 9),
                               -r.get("favourable_days", 0)))
    return {"crop": C.resolve(crop), "risks": scored, "no_model": no_model,
            "season": scans,
            "n_favourable": sum(1 for r in scored
                                if r["band"] == "FAVOURABLE"),
            "n_opened_in_season": sum(1 for x in scans if x.get("opened"))}


# ==============================================================================
# RUNG 1 - THE WITHIN-FIELD ANOMALY
# ==============================================================================

ANOMALY_K = 2.0                 # robust sigmas below the field's own median
ANOMALY_MIN_FRACTION = 0.03     # below this, a "patch" is speckle


def anomaly_threshold(p16: Optional[float], p50: Optional[float],
                      p84: Optional[float], k: float = ANOMALY_K) -> dict:
    """
    The line below which a pixel is unlike the rest of its own field.

    Robust sigma, (p84 - p16) / 2, for the same reason the rest of this
    platform uses it: one waterlogged corner should not widen the spread until
    it stops being an outlier. The reference is THE FIELD ITSELF, so a field
    that is uniformly poor has no anomaly - and that is correct. "Unlike the
    rest of this field" and "bad" are different statements, and only the first
    one is being made.
    """
    if p16 is None or p50 is None or p84 is None:
        return {"status": "NOT AVAILABLE",
                "reason": "no NDVI distribution for this field"}
    sigma = (float(p84) - float(p16)) / 2.0
    if sigma <= 0:
        return {"status": "NOT AVAILABLE",
                "reason": "the field's NDVI has no spread, so nothing in it "
                          "can be an outlier"}
    return {"status": "OK", "threshold": round(float(p50) - k * sigma, 4),
            "median": round(float(p50), 4), "robust_sigma": round(sigma, 4),
            "k": k,
            "basis": f"ARBITRARY: {k} robust sigmas below the field's own "
                     "median. The number controls how often this speaks; it "
                     "carries no agronomic meaning."}


BEARINGS = [
    ("north", "الشمال"), ("north-east", "الشمال الشرقي"),
    ("east", "الشرق"), ("south-east", "الجنوب الشرقي"),
    ("south", "الجنوب"), ("south-west", "الجنوب الغربي"),
    ("west", "الغرب"), ("north-west", "الشمال الغربي"),
]


def bearing(field_centroid, patch_centroid, ar: bool = False) -> Optional[str]:
    """Which part of the field to walk to.

    A coordinate pair is not a direction to anybody standing in a field. This
    turns the offset between the field's centre and the patch's centre into one
    of eight words."""
    if not field_centroid or not patch_centroid:
        return None
    dx = float(patch_centroid[0]) - float(field_centroid[0])   # east positive
    dy = float(patch_centroid[1]) - float(field_centroid[1])   # north positive
    if dx == 0 and dy == 0:
        return None
    ang = math.degrees(math.atan2(dx, dy)) % 360.0   # 0 = north, clockwise
    idx = int((ang + 22.5) // 45) % 8
    return BEARINGS[idx][1] if ar else BEARINGS[idx][0]


def anomaly_patch(area_ha: Optional[float], field_ha: Optional[float],
                  field_centroid=None, patch_centroid=None,
                  min_fraction: float = ANOMALY_MIN_FRACTION) -> dict:
    """
    Report a patch of the field that is unlike the rest of it.

    Says nothing about cause. The sentence it carries is the whole point of
    rung 1: a satellite can say WHERE to walk and HOW BIG, and cannot say WHY.
    Naming a pathogen from these bands would be a guess wearing the clothes of
    a measurement.
    """
    if area_ha is None or not field_ha:
        return {"status": "NOT AVAILABLE",
                "reason": "no anomaly area computed for this field"}
    frac = float(area_ha) / float(field_ha)
    if frac < min_fraction:
        return {"status": "OK", "flagged": False,
                "area_ha": round(float(area_ha), 2),
                "fraction": round(frac, 3),
                "reason": f"below the {min_fraction:.0%} floor - at this size "
                          "a patch is as likely to be speckle as a problem",
                "reason_ar": f"دون أرضية {min_fraction:.0%} — عند هذا الحجم "
                             "يُحتمل أن تكون البقعة تشويشًا لا مشكلة"}
    where = bearing(field_centroid, patch_centroid)
    where_ar = bearing(field_centroid, patch_centroid, ar=True)
    return {
        "status": "OK", "flagged": True,
        "area_ha": round(float(area_ha), 2), "fraction": round(frac, 3),
        "where": where, "where_ar": where_ar,
        "claim": ("Part of this field is unlike the rest of it. This names no "
                  "cause: disease, water shortage, salinity, a blocked outlet, "
                  "pest damage and a badly set drill all look like this from "
                  "10 m. Go and look."),
        "claim_ar": ("جزء من هذا الحقل يختلف عن بقيّته. ولا يسمّي هذا سببًا: "
                     "المرض ونقص الماء والملوحة وانسداد الفتحة وضرر الآفات "
                     "وسوء ضبط البذّارة كلّها تبدو هكذا من ارتفاع 10 أمتار. "
                     "اذهب وانظر."),
    }


# ==============================================================================
# THE LADDER
# ==============================================================================

def diagnose(anomaly: Optional[dict] = None, risk: Optional[dict] = None,
             scouting: Optional[Sequence] = None, crop=None) -> dict:
    """
    The highest claim the evidence supports, and what would lift it.

    claim_level is one of:
        REPORTED  a human named it. The only rung that names a disease.
        ANOMALY   a patch is unlike the rest of the field. Cause unknown.
        RISK      the weather was favourable to something. About the air.
        NONE      nothing to say.

    A satellite NEVER produces REPORTED, whatever the imagery looks like.
    """
    scouting = list(scouting or [])
    named = [s for s in scouting if s.get("problem")]
    anomaly = anomaly or {}
    risk = risk or {}
    favourable = [r for r in risk.get("risks", [])
                  if r.get("band") == "FAVOURABLE"]

    if named:
        latest = sorted(named, key=lambda s: str(s.get("observed_at", "")))[-1]
        return {
            "claim_level": "REPORTED", "problem": latest["problem"],
            "observed_at": latest.get("observed_at"),
            "observer": latest.get("observer", ""),
            "provenance": "REPORTED",
            "headline": f"{label(latest['problem'])} reported on "
                        f"{latest.get('observed_at', 'an unrecorded date')}",
            "headline_ar": f"{label(latest['problem'], ar=True)} مُبلَّغ عنه في "
                           f"{latest.get('observed_at', 'تاريخ غير مسجّل')}",
            "note": "Reported by a person who went and looked. The satellite "
                    "did not name this and cannot.",
            "note_ar": "بلّغ به من ذهب ونظر. القمر لم يسمّه ولا يستطيع.",
        }

    if anomaly.get("flagged"):
        where = anomaly.get("where") or "an unstated part"
        where_ar = anomaly.get("where_ar") or "جزء غير محدّد"
        return {
            "claim_level": "ANOMALY", "problem": None,
            "provenance": "MEASURED",
            "area_ha": anomaly.get("area_ha"),
            "headline": f"about {anomaly.get('area_ha')} ha in the {where} of "
                        "this field is unlike the rest of it",
            "headline_ar": f"نحو {anomaly.get('area_ha')} هكتار في {where_ar} "
                           "من هذا الحقل تختلف عن بقيّته",
            "note": anomaly.get("claim", ""),
            "note_ar": anomaly.get("claim_ar", ""),
            "next_step": "Walk to that part and record what you find. Naming "
                         "it is what lifts this to a reported case.",
            "next_step_ar": "امشِ إلى ذلك الجزء وسجّل ما تجد. تسميته هو ما "
                            "يرفع هذا إلى حالة مُبلَّغ عنها.",
        }

    if favourable:
        top = favourable[0]
        others = len(favourable) - 1
        return {
            "claim_level": "RISK", "problem": top["problem"],
            "provenance": "MODELLED",
            "band": top["band"],
            "headline": (f"weather in the last {top['window_days']} days was "
                         f"favourable to {label(top['problem'])}"
                         + (f" and {others} other" + ("s" if others > 1 else "")
                            if others else "")),
            "headline_ar": (f"طقس الأيام الـ{top['window_days']} الماضية كان "
                            f"مواتيًا {li(label(top['problem'], ar=True))}"
                            + (f" و{others} غيره" if others else "")),
            "note": top["claim"], "note_ar": top["claim_ar"],
            "next_step": f"Scout for: {top['scout_for']}",
            "next_step_ar": f"ابحث في الحقل عن: {top['scout_for_ar']}",
        }

    # A window that opened EARLIER IN THE SEASON, when nothing is favourable
    # now. Weaker than RISK because it is retrospective - it cannot tell
    # anybody to walk out this week - and still the most informative thing
    # available on a report for a season that has finished.
    #
    # This branch exists because the first live run made the NONE text FALSE.
    # It said "no weather window opened" over a season in which three had:
    # thirteen of fourteen days favourable to anthracnose in the fortnight
    # ending 20 August, which is the Gezira rains. A summary that contradicts
    # the data beneath it is worse than no summary.
    opened = sorted([s for s in (risk.get("season") or []) if s.get("opened")],
                    key=lambda s: -s.get("worst_window_days", 0))
    if opened:
        top = opened[0]
        when = top.get("worst_window_end")
        others = len(opened) - 1
        return {
            "claim_level": "SEASON RISK", "problem": top["problem"],
            "provenance": "MODELLED",
            "headline": (
                f"weather favourable to {label(top['problem'])} occurred "
                f"during the season"
                + (f", ending {when}" if when else "")
                + (f", and {others} other" + ("s" if others > 1 else "")
                   if others else "")),
            "headline_ar": (
                f"حدث خلال الموسم طقس مواتٍ "
                f"{li(label(top['problem'], ar=True))}"
                + (f"، انتهى في {when}" if when else "")
                + (f"، و{others} غيره" if others else "")),
            "note": top["claim"], "note_ar": top["claim_ar"],
            "next_step": ("Nothing to do about it now - the window has closed. "
                          "It is what to scout for at the same point next "
                          f"season: {top['scout_for']}"),
            "next_step_ar": ("لا شيء يُفعل الآن — أُغلقت النافذة. وهذا ما "
                             "يُكشف عنه في الوقت نفسه من الموسم القادم: "
                             f"{top['scout_for_ar']}"),
        }

    return {
        "claim_level": "NONE", "problem": None, "provenance": None,
        "headline": "nothing to report", "headline_ar": "لا شيء يُبلَّغ عنه",
        "note": ("No patch unlike the rest of the field, and no weather window "
                 "opened. This is not a clean bill of health: a uniform "
                 "problem across the whole field produces no anomaly, and the "
                 "pests with no weather model produce no risk line."),
        "note_ar": ("لا بقعة تختلف عن بقيّة الحقل، ولم تُفتح نافذة طقس. وهذه "
                    "ليست شهادة سلامة: مشكلة منتظمة تعمّ الحقل كلّه لا تنتج "
                    "شذوذًا، والآفات التي لا نموذج طقس لها لا تنتج سطر خطر."),
    }


REFUSAL = (
    "This tool does not name a disease from satellite imagery. Sentinel-2 sees "
    "reflectance in a few broad bands, and disease, water stress, nitrogen "
    "deficiency, salinity, pest damage and lodging all move those bands "
    "together. Naming a pathogen from them would be a guess wearing the "
    "clothes of a measurement, and the farmer pays for it with a spray.")

REFUSAL_AR = (
    "هذه الأداة لا تسمّي مرضًا من صور الأقمار. Sentinel-2 يرى انعكاسًا في نطاقات "
    "عريضة قليلة، والمرض ونقص الماء ونقص النيتروجين والملوحة وضرر الآفات "
    "والرقاد تحرّك هذه النطاقات معًا. وتسمية مُمرِض منها تخمينٌ يلبس ثوب القياس، "
    "ويدفع المزارع ثمنه رشّةً.")
