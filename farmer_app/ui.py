"""
Presentation layer for the farmer app: styling, bilingual labels, components.

WHY THIS IS A SEPARATE MODULE
-----------------------------
The app file should read as a sequence of decisions about what to show. Mixing
that with colour tokens, CSS and translation tables buries the decisions in
formatting, and the formatting is exactly the part that changes most often.

WHY ARABIC IS THE DEFAULT
-------------------------
The user is a farmer in Sudan. An interface whose chrome is English and whose
advice is Arabic asks the reader to switch language mid-sentence to use their
own tool. The whole page flips - direction, labels, numerals stay Western
because that is what Sudanese agricultural paperwork uses - and English is one
click away for anyone who prefers it.

A NOTE ON THE PALETTE
---------------------
The four status colours are not decorative. They carry the only claim the map
makes, and one of them - grey - means "not measured", a state most farm apps do
not have because they would rather show a confident green. The palette is
defined once, here, and the map, the chips and the legend all read from it, so
the three cannot drift into disagreeing about what a colour means.
"""

from __future__ import annotations

import streamlit as st


# ==============================================================================
# TOKENS
# ==============================================================================

INK = "#1C2321"
INK_SOFT = "#5A6560"
LINE = "#E3DED3"
SURFACE = "#FFFFFF"
PAPER = "#FBFAF7"

# Status colours, matching farmer_app.view exactly. Kept as hex here and as RGBA
# there because pydeck wants RGBA; the pairing is asserted by a test.
STATUS_HEX = {
    "attention": "#C83C2D",
    "watch": "#EBA537",
    "ok": "#46965F",
    "unmeasured": "#828287",
}

# Font stacks, NOT a webfont import.
#
# The first version pulled Inter and Noto Sans Arabic from Google Fonts with an
# @import. That is a network dependency on every page load, and this tool is for
# field offices in Sudan where the connection is the least reliable part of the
# system. It also sends a request to a third party every time a farmer opens
# their own crop data, which is not something to do casually.
#
# These stacks reach a good Arabic face on every platform this will realistically
# run on - Segoe UI on Windows, SF Arabic on macOS and iOS, Noto Sans Arabic on
# Linux and Android - and fall back to Tahoma, which has covered Arabic on every
# Windows since 2000. Nothing is downloaded and nothing is bundled.
SANS = ("system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', "
        "Arial, sans-serif")
SANS_AR = ("'Segoe UI', 'SF Arabic', 'Noto Sans Arabic', 'Noto Naskh Arabic', "
           "'Geeza Pro', Tahoma, system-ui, sans-serif")

CSS = f"""
<style>
/* ============================================================================
   A DESIGN SYSTEM, NOT A PILE OF DECLARATIONS

   What was here before was chosen one rule at a time: six font sizes with no
   ratio between them (some four per cent apart, which the eye cannot use as a
   hierarchy), and four weights of which two - 680 and 650 - are not real
   weights in any of these fonts and were being rounded to 700. Three
   "different" weights rendered as two, so the distinction the code thought it
   was making did not exist on screen.

   Everything below comes from three scales instead.

   TYPE      a 1.2 modular scale from a 14px base. Each step is visibly
             different from its neighbour, which is the whole purpose.
   SPACE     multiples of 4px. Nothing is 7px or 13px because nothing needs
             to be, and arbitrary gaps are what makes a layout feel loose.
   WEIGHT    400 and 600 and 700. Only weights that exist.

   Scoped to this file's own classes and to documented Streamlit test ids.
   Nothing here targets a generated class name; those change between releases,
   and a selector built on one is a promise to break on the next upgrade.
   ============================================================================ */

:root {{
  /* type: 14 * 1.2^n, rounded to the nearest half pixel */
  --t-xs: 11.5px;   /* labels, units, provenance */
  --t-sm: 12.5px;   /* secondary text */
  --t-md: 14px;     /* body */
  --t-lg: 17px;     /* section headings */
  --t-xl: 20px;     /* page title */
  --t-num: 26px;    /* the figures on the stat cards */

  --sp-1: 4px;  --sp-2: 8px;  --sp-3: 12px; --sp-4: 16px;
  --sp-5: 24px; --sp-6: 32px; --sp-7: 48px;

  --r-sm: 8px; --r-md: 12px; --r-lg: 16px; --r-pill: 999px;

  /* Elevation. Cards used to be a 1px outline and nothing else, so nothing
     sat above anything and the eye had no depth to read the layout with.
     Two levels only: resting, and lifted. */
  --e-1: 0 1px 2px rgba(28,35,33,.04), 0 1px 1px rgba(28,35,33,.03);
  --e-2: 0 2px 8px rgba(28,35,33,.07), 0 1px 2px rgba(28,35,33,.04);

  --ink: {INK};
  --soft: {INK_SOFT};
  --line: {LINE};
  --surface: {SURFACE};
  --paper: {PAPER};
}}

.fm {{ font-family: {SANS}; color: var(--ink); }}
.fm.rtl, .rtl {{ direction: rtl; text-align: right; font-family: {SANS_AR}; }}
.rtl * {{ direction: rtl; text-align: right; }}

/* Every figure is tabular. Digits that change width make a column of numbers
   shuffle sideways as the data updates, which reads as instability in the
   measurement rather than in the font. */
.fm .num, .stat .v, .frow .nd, table.vars td.v, .count b {{
  font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1;
}}

[data-testid="stMainBlockContainer"] {{
  padding-top: var(--sp-5); max-width: 1240px;
}}

/* --------------------------------------------------------------- top bar */
.topbar {{
  display: flex; align-items: baseline; gap: var(--sp-3); flex-wrap: wrap;
  border-bottom: 1px solid var(--line);
  padding-bottom: var(--sp-3); margin-bottom: var(--sp-5);
}}
.topbar h1 {{
  font-size: var(--t-xl); font-weight: 700; margin: 0; color: var(--ink);
  letter-spacing: -.015em; line-height: 1.25; flex: none;
}}
.topbar p {{
  width: 100%; margin: var(--sp-1) 0 0; color: var(--soft);
  font-size: var(--t-sm); line-height: 1.6; max-width: 78ch;
}}

.tag {{
  display: inline-flex; align-items: center; gap: var(--sp-1);
  border: 1px solid var(--line); border-radius: var(--r-pill);
  padding: 2px var(--sp-2); font-size: var(--t-xs); color: var(--soft);
  background: var(--paper); white-space: nowrap;
}}
.tag.demo {{
  border-color: {STATUS_HEX['watch']}; color: #8A5B08; background: #FDF6E7;
  font-weight: 600;
}}

/* ------------------------------------------------------------ stat cards */
.statgrid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--sp-3); margin: var(--sp-4) 0 var(--sp-5);
}}
.stat {{
  border: 1px solid var(--line); border-radius: var(--r-md);
  background: var(--surface); padding: var(--sp-3) var(--sp-4);
  box-shadow: var(--e-1);
}}
.stat .k {{
  font-size: var(--t-xs); letter-spacing: .04em; color: var(--soft);
  font-weight: 500; text-transform: none;
}}
.stat .v {{
  font-size: var(--t-num); font-weight: 700; color: var(--ink);
  line-height: 1.15; margin-top: var(--sp-1); letter-spacing: -.02em;
}}
.stat .s {{ font-size: var(--t-xs); color: var(--soft); margin-top: 2px; }}

/* ----------------------------------------------------------- field rows */
/* The main interactive object on the page, and it had no hover state and no
   selected state - so the one thing a reader clicks gave no sign it could be
   clicked, and no sign which one they had chosen. */
.panel {{ max-height: 520px; overflow-y: auto; padding-inline-end: var(--sp-1); }}
.frow {{
  display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap;
  border: 1px solid var(--line);
  border-inline-start: 3px solid var(--accent);
  border-radius: var(--r-sm); background: var(--surface);
  padding: var(--sp-2) var(--sp-3); margin-bottom: var(--sp-2);
  box-shadow: var(--e-1);
  transition: box-shadow .12s ease, border-color .12s ease,
              transform .12s ease;
}}
.frow:hover {{ box-shadow: var(--e-2); transform: translateY(-1px); }}
.frow.sel {{
  border-color: var(--accent);
  box-shadow: var(--e-2), inset 0 0 0 1px var(--accent);
}}
.frow .nm {{ font-weight: 600; color: var(--ink); font-size: var(--t-md); }}
.frow .nd {{
  margin-inline-start: auto; font-size: var(--t-sm); color: var(--soft);
  font-weight: 500;
}}
.frow .sub {{
  width: 100%; font-size: var(--t-xs); color: var(--soft); line-height: 1.55;
  margin-top: 2px;
}}

/* A dot as well as the edge stripe. On a narrow screen the stripe is at the
   far edge of the card and the status is the thing being scanned for. */
.dot {{
  width: 8px; height: 8px; border-radius: 50%; flex: none;
  background: var(--accent);
}}

.count {{ font-size: var(--t-sm); color: var(--soft);
          margin: var(--sp-1) 0 var(--sp-3); }}
.count b {{ color: var(--ink); font-weight: 600; }}

.chip {{
  display: inline-block; padding: 2px var(--sp-2); border-radius: var(--r-pill);
  font-size: var(--t-xs); font-weight: 600; color: #fff; white-space: nowrap;
}}

/* ------------------------------------------------------------ field card */
.fieldcard {{
  border: 1px solid var(--line); border-inline-start: 3px solid var(--accent);
  border-radius: var(--r-md); background: var(--surface);
  padding: var(--sp-3) var(--sp-4); margin-bottom: var(--sp-2);
  box-shadow: var(--e-1);
}}
.fieldcard .top {{ display: flex; align-items: center; gap: var(--sp-2);
                   flex-wrap: wrap; }}
.fieldcard .rank {{ font-size: var(--t-xs); color: var(--soft);
                    font-weight: 500; }}
.fieldcard .name {{ font-size: var(--t-md); font-weight: 600; color: var(--ink); }}
.fieldcard .why {{ font-size: var(--t-sm); color: var(--soft);
                   margin-top: var(--sp-1); line-height: 1.55; }}
.fieldcard ul {{ margin: var(--sp-1) 0 0 0; padding-inline-start: var(--sp-4); }}
.fieldcard li {{ font-size: var(--t-sm); color: var(--ink); margin-bottom: 2px; }}

/* ---------------------------------------------------------------- legend */
.legend {{ display: flex; gap: var(--sp-5); flex-wrap: wrap;
           margin: var(--sp-2) 0; }}
.legend .item {{ display: flex; gap: var(--sp-2); align-items: flex-start;
                 flex: 1 1 200px; }}
.legend .sw {{ width: 10px; height: 10px; border-radius: 3px; margin-top: 4px;
               flex: none; }}
.legend .lb {{ font-size: var(--t-sm); font-weight: 600; color: var(--ink); }}
.legend .ds {{ font-size: var(--t-xs); color: var(--soft); line-height: 1.5; }}

/* -------------------------------------------------------------- headings */
.sechead {{
  font-size: var(--t-lg); font-weight: 700; color: var(--ink);
  margin: var(--sp-6) 0 var(--sp-1); letter-spacing: -.01em;
}}
.sechead + .subtle {{ margin-top: 0; }}
.subtle {{ font-size: var(--t-sm); color: var(--soft); line-height: 1.6;
           margin: var(--sp-1) 0 var(--sp-3); max-width: 78ch; }}

.note {{
  border-inline-start: 2px solid var(--line);
  padding: var(--sp-1) 0 var(--sp-1) var(--sp-3);
  font-size: var(--t-sm); color: var(--soft); line-height: 1.6;
  margin: var(--sp-2) 0; max-width: 78ch;
}}
.note.warn {{ border-inline-start-color: {STATUS_HEX['watch']}; }}
.note.stop {{ border-inline-start-color: {STATUS_HEX['attention']}; }}

/* ------------------------------------------------------- variables table */
table.vars {{ width: 100%; border-collapse: collapse; font-size: var(--t-sm); }}
table.vars th {{
  text-align: start; font-size: var(--t-xs); letter-spacing: .04em;
  color: var(--soft); font-weight: 600;
  border-bottom: 1px solid var(--line); padding: var(--sp-2);
  position: sticky; top: 0; background: var(--paper);
}}
table.vars td {{ padding: var(--sp-2); border-bottom: 1px solid #F2EFE8;
                 color: var(--ink); vertical-align: top; }}
table.vars tr:hover td {{ background: #FCFBF8; }}
table.vars td.v {{ font-weight: 600; }}
table.vars td.meta {{ color: var(--soft); font-size: var(--t-xs); }}
table.vars tr.na td {{ color: var(--soft); font-style: italic; }}
table.vars td.below {{ color: {STATUS_HEX['attention']}; font-weight: 700; }}

/* --------------------------------------------------------------- motion */
/* Somebody who has asked not to see movement should not have to watch a card
   lift under the cursor. */
@media (prefers-reduced-motion: reduce) {{
  .frow {{ transition: none; }}
  .frow:hover {{ transform: none; }}
}}
</style>
"""


# ==============================================================================
# BILINGUAL LABELS
# ==============================================================================

T = {
    "title": ("مراقب المزرعة", "Farm Monitor"),
    # The tagline says what the tool DOES FOR THE READER. The earlier one
    # explained the provenance discipline instead - true, and the first thing
    # a farmer read every morning before reaching an answer. That promise did
    # not weaken; it moved to where it is actually exercised: the sensor and
    # scale columns on every row, and the "About the data" page.
    "tagline": (
        "متابعة حقولك بالأقمار الصناعية: حال كل حقل، واحتياجه من الماء، "
        "وأيّها يستحقّ زيارة اليوم.",
        "Satellite tracking for your fields: how each one is doing, the water "
        "it needs, and which is worth walking to today."),
    "fields": ("الحقول", "Fields"),
    "season": ("الموسم", "Season"),
    "crop": ("المحصول", "Crop"),
    "attention_watch": ("تحتاج انتباهًا / مراقبة", "Attention / watch"),
    "which_first": ("أي حقل أولًا", "Which field first"),
    "field_detail": ("تفاصيل الحقل", "Field detail"),
    "all_variables": ("كل المتغيّرات المقيسة", "All measured variables"),
    "through_season": ("عبر الموسم", "Through the season"),
    "nutrition": ("التغذية", "Nutrition"),
    "yield_": ("الإنتاجية", "Yield"),
    "advisory": ("الإرشاد", "Advisory"),
    "outlook": ("توقّعات 7 أيام", "7-day outlook"),
    "temperature": ("متوسط الحرارة", "Mean temperature"),
    "rain_step": ("متوسط المطر لكل خطوة", "Mean rain per step"),
    "not_claimed": ("ما لا تدّعيه هذه الأداة", "What this tool does not claim"),
    "not_said": ("ما لم يُقَل، ولماذا", "Not said, and why"),
    "page": ("الصفحة", "Page"),
    "page_fields": ("الحقول", "Fields"),
    "page_record": ("إدخال البيانات", "Record data"),
    "stronger_claim": ("لادّعاء أقوى: ", "To make a stronger claim: "),
    "var": ("المتغيّر", "Variable"),
    "value": ("القيمة", "Value"),
    "compared": ("مقارنًا بـ", "Compared with"),
    "reading": ("القراءة", "Reading"),
    "sensor": ("المستشعر", "Sensor"),
    "measured_at": ("قيس عند", "Measured at"),
    "no_verdict": (
        "لم يتيسّر اشتقاق عتبة لهذا الحقل، فالقيم أعلاه معروضة بلا حكم. "
        "وهذا ليس كحقل فُحص فوُجد سليمًا.",
        "No threshold could be derived for this field, so the values above are "
        "reported without a verdict. That is not the same as a field that was "
        "checked and found healthy."),
    "unmeasured_warn": ("لم تُقَس، ولذلك لم تُرتَّب: ",
                        "Not measured, and therefore not ranked: "),
    "no_report": (
        "لم يُعثر على التقرير. شغّل المحرّك أولًا.",
        "Report not found. Run the engine first."),
    "ranking_basis": (
        "الحقول مرتّبة بحسب هبوط النموّ دون عتبة الجوار، ثم بحسب النموّ. "
        "هذا ترتيب لا درجة: لا مقياس صحّة معايَر موجود، ولم يُخترع واحد هنا.",
        "Fields ordered by whether vigour fell below the neighbourhood "
        "threshold, then by vigour. This is an ordering, not a score: no "
        "calibrated health scale exists, and one is not invented here."),
    "unmeasured_note": (
        "الحقول التي تعذّر قياس نموّها تُدرَج على حدة لا في ذيل الترتيب. "
        "غير المقيس ليس سليمًا ولا مريضًا.",
        "Fields with no usable vigour reading are listed separately, not "
        "ranked last. Unmeasured is neither healthy nor sick."),
    "warmer_than": ("أدفأ بـ{d}°م من محيطه",
                    "{d} degC warmer than its surroundings"),
    "draw_here": ("ارسم حقلك", "Draw your field"),
    "draw_help": (
        "استخدم أداة المضلّع أو المستطيل يسار الخريطة لرسم حدود حقلك على صورة "
        "القمر، أو ابحث عن مكانك أعلى اليمين.",
        "Use the polygon or rectangle tool at the left of the map to draw your "
        "field boundary on the satellite image, or search for your location at "
        "the top right."),
    "drawn_count": ("رُسمت {n} قطعة", "{n} drawn"),
    "save_fields": ("احفظ الحقول", "Save fields"),
    "saved_to": ("حُفظت في", "Saved to"),
    "then_run": ("ثم شغّل المحرّك عليها:", "Then run the engine on them:"),
    "rejected": ("مرفوضة", "Rejected"),
    "why_q": ("لماذا؟", "Why?"),
    "method_note": ("عن طريقة الحساب", "About the method"),
    "map_caption": (
        "صورة القمر تُظهر أين حقلك، لا كيف حاله هذا الموسم — الحال في الألوان "
        "والأرقام.",
        "The satellite image shows where your field is, not how it is doing "
        "this season — that is in the colours and the numbers."),
    "sortable": ("جدول قابل للفرز", "Sortable table"),
    "download_csv": ("تنزيل CSV", "Download CSV"),
    "sortable_caveat": (
        "الجدول القابل للفرز يفرز ويُصدَّر، لكنّه لا يستطيع تلوين قراءة دون "
        "العتبة ولا تمييز صفّ غير مقيس — وفيه يتشابه «غير متاح» ورقم منخفض.",
        "The sortable table sorts and exports, but it cannot colour a "
        "below-threshold reading or mark an unmeasured row — in it, "
        "\"not available\" and a merely low number look alike."),
    "no_map": (
        "أشر بـ --fields إلى ملف الحقول لرسم الخريطة. القياسات أدناه لا تعتمد عليه.",
        "Point --fields at the field GeoJSON to draw the map. The measurements "
        "below do not depend on it."),

    # ------------------------------------------------------------- search bar
    "search": ("بحث", "Search"),
    "search_ph": ("اسم الحقل أو المحصول", "Field name or crop"),
    "filters": ("ترشيح", "Filters"),
    "crop_filter": ("المحصول", "Crop"),
    "status_filter": ("الحال", "Status"),
    "date_basis": ("التاريخ حسب", "Date by"),
    "d_greenup": ("الإنبات", "Green-up"),
    "d_harvest": ("الحصاد", "Harvest"),
    "d_last_seen": ("آخر رصد", "Last seen"),
    "d_sown": ("الزراعة", "Sowing"),
    "date_from": ("من", "From"),
    "date_to": ("إلى", "To"),
    "harvest_filter": ("الحصاد", "Harvest"),
    "h_any": ("الكل", "Any"),
    # Not "harvested / standing". Nothing measures "standing": a field cut last
    # week and never written down would sit in it. The option says what is
    # actually known - whether a harvest was reported.
    "h_done": ("حصاد مُبلَّغ", "Harvest reported"),
    "h_none": ("لا حصاد مُبلَّغ", "No harvest reported"),
    "area_filter": ("ضمن الشكل المرسوم", "Inside the drawn shape"),
    "area_hint": (
        "ارسم مضلّعًا على الخريطة ثم فعّل هذا الخيار لقصر القائمة على الحقول "
        "التي يقع مركزها داخله.",
        "Draw a polygon on the map, then switch this on to narrow the list to "
        "the fields whose centre falls inside it."),
    "clear_filters": ("مسح الترشيح", "Clear filters"),
    "results": ("{n} من {total}", "{n} of {total}"),
    "no_match": ("لا حقل يطابق هذا الترشيح.", "No field matches these filters."),
    "unknown_bucket": (
        "مُنحّاة لأنّ القيمة غير مسجّلة، لا لأنّها لا تطابق:",
        "Set aside because the value is not recorded, not because it does not "
        "match:"),
    "u_crop": ("المحصول", "crop"),
    "u_greenup_date": ("تاريخ الإنبات", "green-up date"),
    "u_harvest_date": ("تاريخ الحصاد", "harvest date"),
    "u_last_seen": ("تاريخ آخر رصد", "last-seen date"),
    "u_sown_date": ("تاريخ الزراعة", "sowing date"),
    "u_harvest": ("حصاد مُبلَّغ", "a reported harvest"),
    "u_geometry": ("حدود الحقل", "the field boundary"),

    # ------------------------------------------------------------ field list
    "field_list": ("الحقول", "Fields"),
    "click_map": ("أو انقر حقلًا على الخريطة", "or click a field on the map"),
    "selected": ("المختار", "Selected"),
    "ha": ("هكتار", "ha"),
    "est": ("تقديري", "est."),
    "reported": ("مُبلَّغ", "reported"),

    # ------------------------------------------------------------ about page
    "page_about": ("عن البيانات", "About the data"),
    "about_title": ("عن البيانات والطريقة", "About the data and the method"),
    "about_sub": (
        "كل ما يشرح كيف وُصل إلى رقم، في مكان واحد — خارج شاشة العمل.",
        "Everything that explains how a number was arrived at, in one place - "
        "off the working screen."),
    "this_report": ("هذا التقرير", "This report"),
    "generated": ("وقت التوليد", "Generated"),
    "demo_heading": ("بيانات العرض التوضيحي", "Demonstration data"),
    "demo_chip": ("عرض توضيحي", "DEMO"),
    "demo_line": (
        "بيانات عرض توضيحي فوق حدود مخترعة، لا تخصّ مزرعة أحد.",
        "demonstration data over invented boundaries; it belongs to no farm."),
    "method_link": ("الطريقة في صفحة «عن البيانات».",
                    "The method is on the \"About the data\" page."),

    # ------------------------------------------------------------------ crop
    "crop_of_field": ("محصول هذا الحقل", "This field's crop"),
    "crop_generic": (
        "حُلّلت بمعاملات عامّة لأنّ المحصول غير معروف في المكتبة — وكل رقم "
        "يخصّ المحصول أدناه يقوم عليها.",
        "Analysed with generic parameters because the crop is not in the "
        "library - every crop-specific figure below rests on them."),
    "crop_per_field_help": (
        "هذا محصول التشغيل الافتراضي. وأي حقل يحمل خاصّية crop خاصّة به "
        "تُقدَّم عليه.",
        "This is the run's default. Any field carrying its own `crop` "
        "property overrides it."),

    # --------------------------------------------------------------- disease
    "page_disease": ("الأمراض والآفات", "Disease and pests"),
    "disease_title": ("الأمراض والآفات", "Disease and pests"),
    "anomaly_title": ("شذوذ داخل الحقل", "Within-field anomaly"),
    "weather_windows": ("نوافذ الطقس", "Weather windows"),
    "no_weather_model": (
        "لا نموذج طقس يتنبّأ بها — تُرصد بالكشف الميداني وحده:",
        "Nothing here can predict these - ground scouting only:"),
    "scout_for": ("ابحث عن:", "Scout for:"),
    "refusal_title": ("ما لا تفعله هذه الطبقة", "What this layer will not do"),
    "risk_days": ("{d} من {n} يومًا مواتية", "{d} of {n} days favourable"),
    "disease_ladder": (
        "بلاغ ميداني ← شذوذ داخل الحقل ← نافذة طقس. والأعلى وحده يسمّي مرضًا.",
        "Field report → within-field anomaly → weather window. Only the top "
        "rung names a disease."),

    # -------------------------------------------------------------- scouting
    "scouting": ("كشف ميداني", "Field scouting"),
    "what_found": ("ماذا وجدت؟", "What did you find?"),
    "observed_on": ("تاريخ المشاهدة", "Observed on"),
    "observer": ("المُبلِّغ", "Observer"),
    "record_finding": ("سجّل المشاهدة", "Record the finding"),
    "no_problems_for_crop": (
        "لا آفات مسجّلة لهذا المحصول في المكتبة. ولا تُعرض آفات محصول آخر.",
        "No problems are registered for this crop. Another crop's are not "
        "offered."),

    # --------------------------------------------------------------- changes
    "page_changes": ("ما تغيّر", "What changed"),
    "changes_title": ("ما تغيّر منذ التشغيل السابق",
                      "What changed since the previous run"),
    "previous_report": ("تقرير التشغيل السابق", "Previous report file"),
    "changes_how": (
        "أشر إلى ملف تقرير أقدم لهذه المزرعة لمقارنته بالحالي.",
        "Point at an older report for this farm to compare it with the "
        "current one."),
    "v_declined": ("تراجعت قبل الذروة", "Declined before peak"),
    "v_improved": ("تحسّنت", "Improved"),
    "v_senescence": ("نضج متوقّع", "Ripening as expected"),
    "v_incomparable": ("غير قابلة للمقارنة", "Not comparable"),
    "crossings": ("عبرت عتبتها", "Crossed their threshold"),
    "field_by_field": ("حقلًا حقلًا", "Field by field"),
    "appeared_vanished": ("ظهرت واختفت", "Appeared and vanished"),
    "new_fields": ("جديدة في هذا التشغيل: ", "New in this run: "),
    "gone_fields": ("غابت عن هذا التشغيل: ", "Missing from this run: "),
    "days": ("يومًا", "days"),

    # ------------------------------------------------------------- the runner
    "page_run": ("تشغيل التحليل", "Run the analysis"),
    "run_engine": ("شغّل المحرّك على حقولك", "Run the engine on your fields"),
    "run_needs_fields": (
        "أشر أولًا إلى ملف حقول موجود في الشريط الجانبي، أو ارسم حقولك "
        "واحفظها.",
        "Point at an existing field file in the sidebar first, or draw your "
        "fields and save them."),
    "out_file": ("ملف الخرج", "Output file"),
    "with_series": ("مع السلسلة الزمنية", "With the time series"),
    "with_series_help": (
        "السلسلة المؤرّخة لازمة للتكامل الحقيقي للماء وللإنبات وطول الموسم. "
        "وإطفاؤها أسرع ويُسقط الثلاثة.",
        "The dated series is what the true water integral, the green-up date "
        "and the season length are computed from. Switching it off is faster "
        "and drops all three."),
    "run_estimate": (
        "{n} حقلًا — قدّر نحو {m} دقيقة. والتقدير تقريبي.",
        "{n} fields - allow about {m} minutes. The estimate is rough."),
    "run_now": ("شغّل الآن", "Run now"),
    "running": ("يعمل — لا تغلق الصفحة", "Running - leave this page open"),
    "run_done": ("انتهى. التقرير في", "Finished. The report is at"),
    "run_failed": ("فشل التشغيل (رمز {code}).", "The run failed (code {code})."),
    "run_output": ("مخرجات المحرّك", "Engine output"),

    # ------------------------------------------------------- the field editor
    "name_your_fields": ("سمِّ حقولك", "Name your fields"),
    "editor_help": (
        "اسم الحقل ومحصوله وتاريخ زراعته تُحفظ مع الحدود، وهي ما يبحث به "
        "التطبيق لاحقًا. و«حقل 1» ليس اسمًا يبحث عنه أحد.",
        "The name, crop and sowing date are saved with the boundary and are "
        "what the search later works on. \"Field 1\" is not a name anybody "
        "searches for."),
    "col_name": ("الاسم", "Name"),
    "col_crop": ("المحصول", "Crop"),
    "col_sown": ("تاريخ الزراعة", "Sowing date"),
    "col_tenancy": ("رقم الحواشة", "Tenancy no."),
    "col_area": ("المساحة (هكتار)", "Area (ha)"),
    "heat_over": ("إجهاد حراري فوق", "heat stress above"),
    "run_from_app": (
        "افتح صفحة «تشغيل التحليل» من الشريط الجانبي وشغّل المحرّك على هذا "
        "الملف — لا حاجة إلى الطرفية.",
        "Open \"Run the analysis\" in the sidebar and run the engine on this "
        "file - no terminal needed."),

    # ---------------------------------------------------------- navigation
    # Two views are the product; the rest are tools. The distinction is the
    # whole point of the labels below: a sidebar in which the farm map is the
    # first of seven equal items is an administration console with a map in it.
    "tools": ("أدوات", "Tools"),
    "sources": ("مصادر البيانات", "Where the data comes from"),
    # Streamlit's own multiselect placeholder is the English "Choose options".
    # In an Arabic-first interface it is the most visible untranslated string
    # on the page, and it sits inside the two widgets a reader touches first.
    "any_ph": ("الكل", "any"),
    "synthetic_records": (
        "سجلّات هذا المجلّد اصطناعية — كُتبت بـ tools/seed_demo.py ولم يقسها "
        "أحد. الأرقام المفتوحة أدناه توضّح شكل البوّابة وهي مفتوحة، لا نتيجةً "
        "عن محصول.",
        "The records in this directory are SYNTHETIC - written by "
        "tools/seed_demo.py and measured by nobody. The unlocked figures below "
        "show what a gate looks like when it opens; they are not a result "
        "about a crop."),
    "run_in_console": (
        "تشغيل المحرّك عملُ مشغِّل، ومكانه تطبيق المشغِّل:",
        "Running the engine is an operator job, and it lives in the operator "
        "application:"),
    "console_title": ("مشغِّل مراقب المزرعة", "Farm Monitor console"),
    "console_sub": (
        "أدوات المشغِّل: التشغيل، والسجلّات، والتجميع، والنسخ الاحتياطي، "
        "والمنهجية. أمّا شاشة المزرعة فهي التطبيق الآخر.",
        "Operator tools: runs, records, roll-up, backup and method. The farm "
        "screen is the other application."),
    "back_to_fields": ("← عودة إلى الحقول", "← Back to the fields"),

    # ------------------------------------------------------- the deployment
    "farm_name": ("اسم المزرعة", "Farm name"),
    "not_your_farm": (
        "هذه المزرعة ليست ضمن ما يسمح به حسابك.",
        "This farm is not among those your account may see."),
    "recorded_as": ("سُجّل في تاريخ التشغيلات باسم",
                    "Recorded in the run history as"),
    "not_recorded": ("لم يُسجَّل في تاريخ التشغيلات:",
                     "Not recorded in the run history:"),

    # ----------------------------------------------------------- onboarding
    "welcome": ("ابدأ من هنا", "Start here"),
    "welcome_sub": ("ثلاثة طرق للبدء. اختر واحدًا.", "Three ways in. Pick one."),
    "start_draw": ("ارسم حقولي على الخريطة", "Draw my fields on the map"),
    "start_draw_why": (
        "لا تحتاج ملفًا ولا إحداثيات — ابحث عن مكانك وارسم الحدود على صورة "
        "القمر.",
        "No file and no coordinates needed - find your place and draw the "
        "boundaries on the satellite image."),
    "start_load": ("عندي ملف حقول", "I have a field file"),
    "start_load_why": ("GeoJSON من مكتب المشروع أو من برنامج آخر.",
                       "A GeoJSON from the scheme office or another program."),
    "start_demo": ("أرني العرض التوضيحي", "Show me the demonstration"),
    "start_demo_why": (
        "قياسات أقمار حقيقية فوق حدود مخترعة، لترى شكل الأداة قبل أن ترسم "
        "شيئًا. لا تخصّ مزرعة أحد.",
        "Real satellite measurements over invented boundaries, so you can see "
        "the shape of the tool before drawing anything. They belong to no "
        "farm."),

    # ------------------------------------------------------------- accuracy
    "accuracy": ("اتّفاق القمر مع المُشاهِد", "Satellite vs observer"),
    "accuracy_none": (
        "لا مقارنات واضحة بعد. كل كشف ميداني تسجّله يضيف واحدة.",
        "No clear comparisons yet. Every scouting record you save adds one."),
    "accuracy_help": (
        "الرقم الوحيد هنا الذي يقيس دقّة هذه الأداة بدل أن يدّعيها. والحالات "
        "غير الواضحة تُستبعد منه ولا تُحشر فيه.",
        "The only figure here that MEASURES this tool's accuracy rather than "
        "claiming it. Unclear cases are excluded, never forced into it."),

    # ---------------------------------------------------------- aggregation
    "page_units": ("حسب الوحدة الإدارية", "By administrative unit"),
    "roll_up_to": ("جمّع حسب", "Roll up to"),
    "unit_withheld": ("حُجب المتوسّط", "mean withheld"),
    "unplaced_fields": ("حقول خارج الهرم", "Fields outside the hierarchy"),
    "hierarchy": ("الهرم الإداري", "Hierarchy"),
    "coverage": ("التغطية", "coverage"),

    # --------------------------------------------------------------- export
    "export": ("تصدير", "Export"),
    "export_html": ("نزّل ملف HTML مكتفيًا بذاته",
                    "Download a self-contained HTML file"),
    "export_why": (
        "ملف واحد يحوي بياناته وخريطته: يُفتح بلا خادم وبلا إنترنت، ويُطبع، "
        "ويُنسخ على ذاكرة، ويُفتح بعد سنوات بلا هذا البرنامج.",
        "One file with its data and its map inside it: opens with no server "
        "and no internet, prints, copies to a memory stick, and opens years "
        "from now without this program."),

    # --------------------------------------------------------------- backup
    "page_backup": ("نسخة احتياطية", "Backup"),
    "backup_what": ("ما يُفقد إن ضاع الجهاز", "Lost if this machine is"),
    "backup_make": ("أنشئ النسخة", "Create the archive"),
    "backup_verify": ("تحقّق من نسخة", "Verify an archive"),
    "backup_done": ("كُتبت النسخة", "Archive written"),
    "photographs": ("صور", "photographs"),

    # --------------------------------------------------------------- mobile
    "compact": ("عرض مختصر", "Compact view"),
    "compact_help": (
        "قائمة للقراءة على الهاتف: أي حقل، وماذا تفعل. بلا خريطة وبلا رسم.",
        "A read-only list for a phone: which field, and what to do. No map, "
        "no drawing."),
}


def t(key: str, ar: bool) -> str:
    pair = T.get(key)
    if not pair:
        return key
    return pair[0] if ar else pair[1]


# ==============================================================================
# COMPONENTS
# ==============================================================================

def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def _cls(ar: bool, extra: str = "") -> str:
    """Class attribute for a component wrapper. `fm` carries the font
    stack; `rtl` flips direction. Both are this file's own classes."""
    return f'class="fm {extra}{" rtl" if ar else ""}"'


def topbar(ar: bool, demo: bool = False) -> None:
    """
    The name and one line saying what the tool is for. Nothing else.

    There used to be a row of chips here - season, crop, field count, and a
    demonstration pill. All of it is gone, including the pill.

    The demonstration label is an obligation, not decoration: engine output
    over invented boundaries is the most misleading thing this tool can
    produce, and it has to say so where a reader will see it. But an
    obligation does not require its own element. It is a clause on the line
    that was already there, so the header is one heading and one line whether
    the data is real or not.
    """
    line = t("tagline", ar)
    if demo:
        line += " — " + t("demo_line", ar)
    d = ' dir="rtl"' if ar else ""
    st.markdown(
        f'<div class="topbar fm{" rtl" if ar else ""}"{d}>'
        f'<h1>{t("title", ar)}</h1>'
        f'<p>{line}</p></div>',
        unsafe_allow_html=True)


def result_count(n: int, total: int, ar: bool) -> None:
    d = ' dir="rtl"' if ar else ""
    st.markdown(f'<div class="count fm{" rtl" if ar else ""}"{d}>'
                f'<b>{n}</b> / {total} — {t("field_list", ar)}</div>',
                unsafe_allow_html=True)


def field_row(name, status_key, status_label, tags=(), right="",
              sub="", selected=False, ar=False) -> None:
    """One row of the field list: colour, name, facts, and the reason for the
    colour. The colour is the same one the map uses - a list and a map that
    disagree are worse than either alone."""
    accent = STATUS_HEX.get(status_key, STATUS_HEX["unmeasured"])
    d = ' dir="rtl"' if ar else ""
    chips = "".join(f'<span class="tag">{x}</span>' for x in tags if x)
    st.markdown(
        f'<div class="frow fm{" rtl" if ar else ""}{" sel" if selected else ""}"'
        f' style="--accent:{accent}"{d}>'
        # A dot as well as the edge stripe. On a narrow screen the stripe is at
        # the far edge of the card, and the status is the thing being scanned
        # for - it belongs beside the name, not at the margin.
        f'<span class="dot"></span>'
        f'<span class="chip" style="background:{accent}">{status_label}</span>'
        f'<span class="nm">{name}</span>{chips}'
        + (f'<span class="nd">{right}</span>' if right else "")
        + (f'<div class="sub">{sub}</div>' if sub else "")
        + "</div>",
        unsafe_allow_html=True)


def stats(items) -> None:
    """items: list of (label, value, sublabel|None)."""
    cells = "".join(
        f'<div class="stat"><div class="k">{k}</div>'
        f'<div class="v">{v}</div>'
        + (f'<div class="s">{s}</div>' if s else "")
        + "</div>"
        for k, v, s in items)
    st.markdown(f'<div class="statgrid fm">{cells}</div>', unsafe_allow_html=True)


def section(title: str, subtitle: str = "", ar: bool = False) -> None:
    d = ' dir="rtl"' if ar else ""
    st.markdown(f'<div class="sechead fm{" rtl" if ar else ""}"{d}>{title}</div>',
                unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="subtle fm{" rtl" if ar else ""}"{d}>{subtitle}</div>',
                    unsafe_allow_html=True)


def note(text: str, kind: str = "", ar: bool = False) -> None:
    d = ' dir="rtl"' if ar else ""
    st.markdown(f'<div class="note fm {kind}{" rtl" if ar else ""}"{d}>{text}</div>',
                unsafe_allow_html=True)


def field_card(rank, name, status_label, status_key, vigour, why,
               drivers, ar=False) -> None:
    accent = STATUS_HEX.get(status_key, STATUS_HEX["unmeasured"])
    d = ' dir="rtl"' if ar else ""
    items = "".join(f"<li>{x}</li>" for x in drivers)
    st.markdown(
        f'<div class="fieldcard fm{" rtl" if ar else ""}" '
        f'style="--accent:{accent}"{d}>'
        f'<div class="top">'
        f'<span class="rank">#{rank}</span>'
        f'<span class="name">{name}</span>'
        f'<span class="chip" style="background:{accent}">{status_label}</span>'
        f'<span class="rank">NDVI {vigour:.3f}</span>'
        f'</div>'
        f'<div class="why">{why}</div>'
        + (f"<ul>{items}</ul>" if items else "")
        + "</div>",
        unsafe_allow_html=True)


def legend(entries, ar=False) -> None:
    """entries: list of (status_key, label, meaning)."""
    d = ' dir="rtl"' if ar else ""
    html = "".join(
        f'<div class="item">'
        f'<div class="sw" style="background:{STATUS_HEX[k]}"></div>'
        f'<div><div class="lb">{lbl}</div><div class="ds">{meaning}</div></div>'
        f'</div>'
        for k, lbl, meaning in entries)
    st.markdown(f'<div class="legend fm{" rtl" if ar else ""}"{d}>{html}</div>',
                unsafe_allow_html=True)


def variables_table(rows, ar=False) -> None:
    """Render the measurement rows as a styled table.

    Built as HTML rather than st.dataframe so a BELOW-threshold reading can be
    coloured and an unavailable row can be visibly set in italic grey. In a
    dataframe those two states look identical to a number that is simply low,
    which is the distinction the whole engine turns on.
    """
    d = ' dir="rtl"' if ar else ""
    head = "".join(f"<th>{h}</th>" for h in (
        t("var", ar), t("value", ar), t("compared", ar),
        t("reading", ar), t("sensor", ar), t("measured_at", ar)))
    body = []
    for r in rows:
        value = str(r.get("value", ""))
        na = value.startswith("not available") or value.startswith("غير متاح")
        below = ("BELOW" in str(r.get("verdict", ""))
                 or "دون العتبة" in str(r.get("verdict", "")))
        # Why a row is unavailable rides ON the row, as a hover title and a
        # marker, instead of in a paragraph underneath the table. It is the one
        # piece of method text that changes what the reader does - wait for a
        # clear scene, or go and look - so it stays at the point of use.
        reason = str(r.get("reason", "") or "").replace('"', "&quot;")
        mark = f'<span class="q" title="{reason}">?</span>' if reason else ""
        body.append(
            f'<tr class="{"na" if na else ""}"'
            + (f' title="{reason}"' if reason else "") + ">"
            f'<td>{r["variable"]}{mark}</td>'
            f'<td class="v">{r["value"]}</td>'
            f'<td class="meta">{r["threshold"]}</td>'
            f'<td class="{"below" if below else "meta"}">{r["verdict"]}</td>'
            f'<td class="meta">{r["sensor"]}</td>'
            f'<td class="meta">{r["scale"]}</td>'
            f"</tr>")
    st.markdown(
        f'<table class="vars fm{" rtl" if ar else ""}"{d}><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>',
        unsafe_allow_html=True)
