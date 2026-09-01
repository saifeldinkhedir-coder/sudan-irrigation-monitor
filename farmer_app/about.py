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
