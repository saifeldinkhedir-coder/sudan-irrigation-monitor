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

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {{
  font-family: 'Inter', 'Noto Sans Arabic', system-ui, sans-serif;
}}
.rtl, .rtl * {{ direction: rtl; text-align: right;
                font-family: 'Noto Sans Arabic', 'Inter', sans-serif; }}

/* Streamlit's default block padding is generous at the top and mean at the
   sides; this reverses it so content breathes horizontally on a laptop. */
.block-container {{ padding-top: 2.2rem; max-width: 1240px; }}

.hero {{
  border: 1px solid {LINE}; border-radius: 14px; background: {SURFACE};
  padding: 1.1rem 1.3rem; margin-bottom: 1.1rem;
}}
.hero h1 {{ font-size: 1.55rem; margin: 0 0 .25rem 0; color: {INK};
            letter-spacing: -.01em; }}
.hero p {{ margin: 0; color: {INK_SOFT}; font-size: .88rem; line-height: 1.5; }}

.statgrid {{ display: flex; gap: .7rem; flex-wrap: wrap; margin: .9rem 0 1.1rem; }}
.stat {{
  flex: 1 1 150px; border: 1px solid {LINE}; border-radius: 12px;
  background: {SURFACE}; padding: .7rem .9rem;
}}
.stat .k {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .07em;
            color: {INK_SOFT}; }}
.stat .v {{ font-size: 1.45rem; font-weight: 650; color: {INK};
            line-height: 1.25; margin-top: .15rem; }}
.stat .s {{ font-size: .74rem; color: {INK_SOFT}; }}

.fieldcard {{
  border: 1px solid {LINE}; border-left: 5px solid var(--accent);
  border-radius: 12px; background: {SURFACE};
  padding: .75rem .95rem; margin-bottom: .55rem;
}}
.fieldcard .top {{ display: flex; align-items: baseline; gap: .6rem;
                   flex-wrap: wrap; }}
.fieldcard .rank {{ font-size: .78rem; color: {INK_SOFT}; font-weight: 600; }}
.fieldcard .name {{ font-size: 1.02rem; font-weight: 650; color: {INK}; }}
.fieldcard .why {{ font-size: .8rem; color: {INK_SOFT}; margin-top: .3rem;
                   line-height: 1.5; }}
.fieldcard ul {{ margin: .4rem 0 0 0; padding-inline-start: 1.1rem; }}
.fieldcard li {{ font-size: .82rem; color: {INK}; margin-bottom: .15rem; }}

.chip {{
  display: inline-block; padding: .12rem .55rem; border-radius: 999px;
  font-size: .72rem; font-weight: 600; color: #fff; white-space: nowrap;
}}

.legend {{ display: flex; gap: 1.1rem; flex-wrap: wrap; margin: .5rem 0 .2rem; }}
.legend .item {{ display: flex; gap: .45rem; align-items: flex-start;
                 flex: 1 1 210px; }}
.legend .sw {{ width: 13px; height: 13px; border-radius: 3px; margin-top: .22rem;
               flex: none; }}
.legend .lb {{ font-size: .78rem; font-weight: 650; color: {INK}; }}
.legend .ds {{ font-size: .73rem; color: {INK_SOFT}; line-height: 1.45; }}

.sechead {{ font-size: 1.05rem; font-weight: 680; color: {INK};
            margin: 1.5rem 0 .15rem; }}
.sechead + .subtle {{ margin-top: 0; }}
.subtle {{ font-size: .8rem; color: {INK_SOFT}; line-height: 1.5;
           margin: .2rem 0 .7rem; }}

.note {{ border-inline-start: 3px solid {LINE}; padding: .35rem 0 .35rem .7rem;
         font-size: .79rem; color: {INK_SOFT}; line-height: 1.55;
         margin: .5rem 0; }}
.note.warn {{ border-inline-start-color: {STATUS_HEX['watch']}; }}
.note.stop {{ border-inline-start-color: {STATUS_HEX['attention']}; }}

/* The variables table: dense, aligned, and quiet enough to scan. */
table.vars {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
table.vars th {{
  text-align: start; font-size: .7rem; text-transform: uppercase;
  letter-spacing: .06em; color: {INK_SOFT}; font-weight: 600;
  border-bottom: 1px solid {LINE}; padding: .4rem .5rem;
}}
table.vars td {{ padding: .42rem .5rem; border-bottom: 1px solid #F2EFE8;
                 color: {INK}; vertical-align: top; }}
table.vars td.v {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
table.vars td.meta {{ color: {INK_SOFT}; font-size: .76rem; }}
table.vars tr.na td {{ color: {INK_SOFT}; font-style: italic; }}
table.vars td.below {{ color: {STATUS_HEX['attention']}; font-weight: 650; }}
</style>
"""


# ==============================================================================
# BILINGUAL LABELS
# ==============================================================================

T = {
    "title": ("مراقب المزرعة", "Farm Monitor"),
    "tagline": (
        "مراقبة المحاصيل بالأقمار الصناعية. كل رقم يذكر المستشعر الذي جاء منه "
        "والمقياس الذي قيس به؛ وما تعذّر قياسه يقول ذلك بدل أن يعرض رقمًا.",
        "Satellite crop monitoring. Every figure names the sensor it came from "
        "and the scale it was measured at; anything that could not be measured "
        "says so rather than showing a number."),
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
    "no_map": (
        "أشر بـ --fields إلى ملف الحقول لرسم الخريطة. القياسات أدناه لا تعتمد عليه.",
        "Point --fields at the field GeoJSON to draw the map. The measurements "
        "below do not depend on it."),
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
    return f'class="{extra} rtl"' if ar else f'class="{extra}"'


def hero(ar: bool) -> None:
    st.markdown(
        f'<div class="hero" {"dir=rtl" if ar else ""}>'
        f'<h1>{"🌾 " if not ar else ""}{t("title", ar)}{" 🌾" if ar else ""}</h1>'
        f'<p>{t("tagline", ar)}</p></div>',
        unsafe_allow_html=True)


def stats(items) -> None:
    """items: list of (label, value, sublabel|None)."""
    cells = "".join(
        f'<div class="stat"><div class="k">{k}</div>'
        f'<div class="v">{v}</div>'
        + (f'<div class="s">{s}</div>' if s else "")
        + "</div>"
        for k, v, s in items)
    st.markdown(f'<div class="statgrid">{cells}</div>', unsafe_allow_html=True)


def section(title: str, subtitle: str = "", ar: bool = False) -> None:
    d = ' dir="rtl"' if ar else ""
    st.markdown(f'<div class="sechead"{d}>{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="subtle"{d}>{subtitle}</div>',
                    unsafe_allow_html=True)


def note(text: str, kind: str = "", ar: bool = False) -> None:
    d = ' dir="rtl"' if ar else ""
    st.markdown(f'<div class="note {kind}"{d}>{text}</div>',
                unsafe_allow_html=True)


def field_card(rank, name, status_label, status_key, vigour, why,
               drivers, ar=False) -> None:
    accent = STATUS_HEX.get(status_key, STATUS_HEX["unmeasured"])
    d = ' dir="rtl"' if ar else ""
    items = "".join(f"<li>{x}</li>" for x in drivers)
    st.markdown(
        f'<div class="fieldcard" style="--accent:{accent}"{d}>'
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
    st.markdown(f'<div class="legend"{d}>{html}</div>', unsafe_allow_html=True)


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
        na = str(r.get("value", "")).startswith("not available")
        below = "BELOW" in str(r.get("verdict", ""))
        body.append(
            f'<tr class="{"na" if na else ""}">'
            f'<td>{r["variable"]}</td>'
            f'<td class="v">{r["value"]}</td>'
            f'<td class="meta">{r["threshold"]}</td>'
            f'<td class="{"below" if below else "meta"}">{r["verdict"]}</td>'
            f'<td class="meta">{r["sensor"]}</td>'
            f'<td class="meta">{r["scale"]}</td>'
            f"</tr>")
    st.markdown(
        f'<table class="vars"{d}><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>',
        unsafe_allow_html=True)
