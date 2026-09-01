"""
"About the data" - where the method text lives now.

WHY THIS PAGE EXISTS
--------------------
Every paragraph on this page used to sit on the working screen, next to the
number it described. That was the wrong place for it twice over. A farmer
opening the app at six in the morning to find out which field needs water was
reading a note on time-integrated evapotranspiration before reaching the
answer, and an auditor who genuinely wanted the method had to hunt for it in
seven different expanders.

So the method moved here, whole, in one place - and the working screen kept
only what changes a decision. The test of which is which:

    Would knowing this change what the reader DOES today?
        yes  ->  it stays on the working screen
        no   ->  it belongs on this page

"Not measured is not healthy" changes what you do: you go and look. "Green-up
is the first crossing of half the seasonal amplitude" does not: it explains how
a date was arrived at, and the person who needs that is checking the tool, not
using it.

WHAT WAS NOT DONE
-----------------
Nothing was deleted. A platform whose entire argument is that its numbers can
be audited cannot bury the basis for them; that would trade a real property for
a tidier screen. Every sentence removed from the working screen is reproduced
here in full, including the ones that are unflattering.
"""

from __future__ import annotations

import streamlit as st

import ui


SECTIONS = {
    "what_this_is": (
        "ما هذه الأداة",
        "What this tool is",
        "أداة رصد بالأقمار الصناعية لحقول مروية. تقيس ما تستطيع الأقمار قياسه — "
        "نموّ الغطاء النباتي، رطوبته، حرارة السطح، المطر، واحتياج المحصول من "
        "الماء — وتقارن كل حقل بجواره وببقية حقول المزرعة. ولا تزور الحقل، "
        "ولا تحلّ محلّ من يزوره.",
        "A satellite monitoring tool for irrigated fields. It measures what "
        "satellites can measure - canopy vigour, canopy moisture, surface "
        "temperature, rainfall and crop water requirement - and compares each "
        "field with its own surroundings and with the rest of the farm. It does "
        "not visit the field, and it does not replace the person who does."),

    "provenance": (
        "من أين يأتي كل رقم",
        "Where each number comes from",
        "كل صفّ في جدول المتغيّرات يحمل عمودين: المستشعر الذي جاء منه الرقم، "
        "والمقياس الذي قيس به. وهذا ليس تزيينًا: قراءة حرارية عند 100 متر وقراءة "
        "نموّ عند 10 أمتار ليستا نوعًا واحدًا من الكلام عن حقل صغير. وما تعذّر "
        "قياسه يُكتب «غير متاح» مع سببه، ولا يُملأ برقم.",
        "Every row of the variables table carries two columns: the sensor the "
        "number came from, and the scale it was measured at. That is not "
        "decoration - a 100 m thermal reading and a 10 m vigour reading are not "
        "the same kind of statement about a small field. Anything that could "
        "not be measured reads \"not available\" with its reason, and is never "
        "filled in with a number."),

    "ranking": (
        "كيف رُتّبت الحقول",
        "How the fields were ordered",
        "الحقول مرتّبة بحسب هبوط النموّ دون العتبة المشتقّة من جوار الحقل نفسه، "
        "ثم بحسب النموّ. وهذا ترتيب لا درجة: لا يوجد مقياس صحّة معايَر، ولم "
        "يُخترع واحد هنا. والحقول التي تعذّر قياس نموّها تُدرَج على حدة لا في ذيل "
        "الترتيب — غير المقيس ليس سليمًا ولا مريضًا.",
        "Fields are ordered by whether vigour fell below the threshold derived "
        "from the field's own neighbourhood, then by vigour. This is an "
        "ordering, not a score: no calibrated health scale exists, and one was "
        "not invented here. Fields with no usable vigour reading are listed "
        "separately rather than ranked last - unmeasured is neither healthy "
        "nor sick."),

    "water": (
        "حساب الماء",
        "The water calculation",
        "ET0 محسوب بمعادلة بنمان-مونتيث (FAO-56) من ERA5-Land. و ETc تكامل عبر "
        "الموسم: مجموع Kcb(t)×ET0(t) على كل يوم، حيث Kcb مشتقّ من NDVI ومُستكمل "
        "بين المشاهد. واستخدام متوسط Kcb للموسم بدلًا من التكامل يعطي فرقًا "
        "منهجيًا يبلغ نحو الخُمس على حقل حقيقي، ولذلك تُعلَّم الطريقة التقريبية "
        "حين تُستخدم. والرقم احتياج، لا ما وصل الحقل فعلًا.",
        "ET0 is Penman-Monteith (FAO-56) from ERA5-Land. ETc is integrated "
        "across the season: the sum of Kcb(t) x ET0(t) over every day, with Kcb "
        "derived from NDVI and interpolated between scenes. Using a season-mean "
        "Kcb instead of integrating differs systematically by about a fifth on "
        "a real field, so the approximate method is flagged wherever it is "
        "used. The figure is a REQUIREMENT, not what the field received."),

    "phenology": (
        "الإنبات وطول الموسم",
        "Green-up and season length",
        "يوم الإنبات هو أول عبور لنصف سعة الموسم في منحنى NDVI، وهو اصطلاح لا "
        "قياس. والأيام تُعدّ من بداية نافذة الموسم، وفجوات السحب قد تزيح التاريخ "
        "بمقدار الفترة بين مشهدين صافيين. وتاريخ الحصاد المتوقّع مشتقّ من هذين، "
        "ويُعلَّم «تقديري» أينما ظهر — والتاريخ الذي يُدخله المزارع يُعلَّم «مُبلَّغ» "
        "ويُخزَّن منفصلًا.",
        "Green-up day is the first crossing of half the seasonal amplitude in "
        "the NDVI curve - a convention, not a measurement. Days are counted "
        "from the start of the season window, and cloud gaps can shift the date "
        "by the interval between two clear scenes. The expected harvest date is "
        "derived from those two and is labelled ESTIMATED wherever it appears; "
        "a date the farmer enters is labelled REPORTED and stored separately."),

    "crop": (
        "المحصول",
        "The crop",
        "كل حقل يُحلَّل بمحصوله هو، لا بمحصول التشغيل. فالحيازة تدور بين القطن "
        "والذرة والقمح والفول، وإعطاء قطعة قمح عتبةَ الذرة الحرارية (38 درجة) "
        "يعطي رقمًا خاطئًا لا رقمًا ناقصًا — القمح يبدأ فقدان الحبّ عند 32. "
        "ومعاملات المحاصيل من FAO-56 وأرقام منشورة تقليدية، لا من تجارب "
        "سودانية؛ وعتبة الحرارة صفة صنف بقدر ما هي صفة نوع، وأصناف الجزيرة "
        "انتُخبت تحت الحرارة قرنًا كاملًا. ومحصول لا تعرفه المكتبة يُحلَّل "
        "بمعاملات عامّة، ويقول التطبيق ذلك على الحقل نفسه.",
        "Each field is analysed as ITS OWN crop, not the run's. A tenancy "
        "rotates cotton, sorghum, wheat and faba bean, and giving a wheat "
        "block sorghum's 38 degC heat threshold produces a wrong number rather "
        "than a missing one - wheat starts losing grain at 32. The crop "
        "parameters are FAO-56 and conventional published figures, not "
        "Sudanese trial data; a heat threshold is as much a variety property "
        "as a species one, and Gezira varieties have been selected under heat "
        "for a century. A crop the library does not know is analysed with "
        "generic parameters, and the app says so on the field itself."),

    "disease": (
        "الأمراض والآفات — وما لا يُقال",
        "Disease and pests - and what is not said",
        "لا تسمّي هذه الأداة مرضًا من صور الأقمار. وليس هذا نقصًا في البرنامج "
        "يرفعه نموذج أكبر: Sentinel-2 يرى انعكاسًا في نطاقات عريضة قليلة، "
        "والمرض ونقص الماء ونقص النيتروجين والملوحة وضرر الآفات والرقاد تحرّك "
        "هذه النطاقات معًا ولا تفصلها. والطبقة ثلاث درجات: (1) شذوذ داخل "
        "الحقل — بقعة تختلف عن بقيّة الحقل نفسه، تُعطى مساحةً وجهةً ولا "
        "تُسمّى سببًا؛ (2) خطر من الطقس — نافذة حرارة ورطوبة مواتية لمُمرِض "
        "بعينه، وهي وصف للهواء يصدق على كل حقل سليم تحت السماء نفسها؛ "
        "(3) بلاغ ميداني — وهو وحده ما يسمّي مرضًا موجودًا. ونماذج الطقس "
        "منشورة من خارج السودان وغير مُتحقَّق منها هنا، وبلل الورقة مُقدَّر "
        "بالمطر أو بالرطوبة النسبية العظمى، وهو تقدير يخطئ في ليلة عاصفة.",
        "This tool does not name a disease from satellite imagery. That is not "
        "a software shortfall a bigger model would lift: Sentinel-2 sees "
        "reflectance in a few broad bands, and disease, water stress, nitrogen "
        "deficiency, salinity, pest damage and lodging all move those bands "
        "together. The layer has three rungs: (1) a within-field ANOMALY - a "
        "patch unlike the rest of the same field, given a size and a direction "
        "and no cause; (2) weather RISK - a temperature and wetness window "
        "favourable to a named pathogen, which describes the air and is "
        "equally true of every healthy field under that sky; (3) a field "
        "REPORT - the only rung that names a disease as present. The weather "
        "models are published from outside Sudan and unvalidated here, and "
        "leaf wetness is a proxy from rain or maximum relative humidity that "
        "will be wrong on a windy night."),

    "change": (
        "ما تغيّر منذ التشغيل السابق",
        "What changed since the previous run",
        "الهبوط في NDVI ليس خبرًا سيّئًا بالضرورة: الذرة تنبت في أغسطس وتبلغ "
        "الذروة في أكتوبر ثمّ تنضج قصدًا حتى الحصاد. وكاشف تغيّر يُعلّم كل "
        "هبوط سيُعلّم كل حقل في المشروع كل خريف، ويدفن الحقل الوحيد الذي "
        "يفشل فعلًا. فالهبوط بعد الذروة يُقرأ نضجًا، وقبلها تراجعًا — الرقم "
        "نفسه وحكمان، والفاصل بينهما تاريخ الإنبات المحسوب أصلًا. والتغيّر "
        "الأصغر من تشتّت الحقل نفسه يُقرأ ثباتًا. والتواريخ تواريخ المشاهد لا "
        "تواريخ التشغيل: تشغيلان بفارق أسبوع قد يقومان على مشهدين بفارق شهر.",
        "A fall in NDVI is not necessarily bad news: sorghum greens up in "
        "August, peaks in October, and then senesces on purpose all the way to "
        "harvest. A change detector that flags every decline will flag every "
        "field on the scheme every autumn, and bury the one field that is "
        "actually failing. So a fall past the peak is read as ripening and a "
        "fall before it as a decline - the same number, two verdicts, "
        "separated by the green-up date the engine already computed. A change "
        "smaller than the field's own spread is read as steady. Dates are "
        "scene dates, not run dates: two runs a week apart can rest on scenes "
        "a month apart."),

    "search": (
        "ما يفعله البحث بالمجهول",
        "What the search does with what it does not know",
        "حين تُرشّح بالمحصول أو بالتاريخ أو بالحصاد، فالحقول التي لا قيمة لها في "
        "ذلك الحقل لا تُسقَط بصمت — تُعرَض على حدة تحت «مجهول». إسقاطها بصمت "
        "يقول للقارئ إنّها لا تطابق، والحقيقة أنّ أحدًا لم يقل. والاختيار بالمضلّع "
        "يختبر وقوع مركز الحقل داخل الشكل المرسوم، فحقلٌ نصفه داخل الشكل إمّا "
        "داخل أو خارج، مرّة واحدة.",
        "When you filter by crop, date or harvest, fields with no value for "
        "that attribute are not dropped silently - they are shown separately "
        "under \"unknown\". Dropping them silently tells the reader they do not "
        "match, when the truth is that nobody said. Polygon selection tests "
        "whether the field's CENTROID falls inside the drawn shape, so a "
        "half-covered field is either in or out, once."),
}


def render(report: dict, ar: bool = False) -> None:
    """The whole method, in one place, in the reader's language."""
    ui.section(ui.t("about_title", ar), ui.t("about_sub", ar), ar)

    for _key, (ar_h, en_h, ar_body, en_body) in SECTIONS.items():
        st.markdown(f"#### {ar_h if ar else en_h}")
        ui.note(ar_body if ar else en_body, "", ar)

    # ------------------------------------------------------------- the report
    st.divider()
    st.markdown(f"#### {ui.t('this_report', ar)}")
    # The engine writes `sensors` as a mapping of instrument to what it
    # contributes; older reports wrote a bare list. Both are rendered, because
    # a page about provenance that drops the sensor list on a format change is
    # worse than useless.
    sensors = report.get("sensors") or {}
    if isinstance(sensors, dict) and sensors:
        st.table([{ui.t("sensor", ar): k, ui.t("value", ar): v}
                  for k, v in sensors.items()])
    elif sensors:
        st.markdown("`" + "`  ·  `".join(str(s) for s in sensors) + "`")
    meta = {
        ui.t("season", ar): f'{(report.get("season") or {}).get("start", "?")}'
                            f' → {(report.get("season") or {}).get("end", "?")}',
        ui.t("crop", ar): report.get("crop", "—"),
        ui.t("fields", ar): report.get("n_fields", 0),
        "Earth Engine": report.get("gee_project", "—"),
        ui.t("generated", ar): report.get("generated_utc", "—"),
    }
    st.table([{ui.t("var", ar): k, ui.t("value", ar): v}
              for k, v in meta.items()])

    # A demonstration report is engine output over invented boundaries. The
    # paragraph came off the working screen; it does not come off the record.
    if report.get("note"):
        st.markdown(f"#### {ui.t('demo_heading', ar)}")
        ui.note(report.get("note_ar") if ar and report.get("note_ar")
                else report["note"], "warn", ar)

    # The list of things the tool does NOT claim is the last list that should
    # reach a farmer in a language they may not read. Falls back to English
    # only when the report predates the Arabic list - visibly, not silently.
    limits = ((report.get("limitations_ar") if ar else None)
              or report.get("limitations") or [])
    if limits:
        st.markdown(f"#### {ui.t('not_claimed', ar)}")
        for lim in limits:
            st.markdown(f"- {lim}")
