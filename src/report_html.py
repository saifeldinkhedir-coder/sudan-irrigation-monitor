"""
The report as one self-contained HTML file: readable offline, and printable.

WHY THIS BEATS A BETTER DASHBOARD
---------------------------------
Streamlit is a live websocket application. Over the connection a Gezira block
office actually has, that is close to the worst possible architecture: the page
is blank until the socket comes up, and it dies when the socket drops. Every
improvement to the dashboard is an improvement to something that may not load.

One HTML file with its data, its styling and its map inside it loads from a USB
stick, survives being emailed, opens in five years without this program, and
needs no server at all. For a field office it is not a fallback - it is the
better artefact.

WHY IT PRINTS
-------------
The Gezira Scheme is a bureaucracy that runs on paper, and the meetings where
decisions about a block are taken do not have a laptop in them. A one-page
Arabic sheet per field is worth more there than three dashboard features. The
print stylesheet is not decoration: page breaks are placed so a field never
splits across two sheets, and the colours are chosen to survive a monochrome
office printer - each status carries a MARK as well as a colour, because a red
field and a green field are the same grey on the photocopy that reaches the
meeting.

WHY THE MAP IS AN SVG AND NOT A TILE LAYER
------------------------------------------
Satellite tiles need the network, which is the thing this file exists to do
without. The map here is the field boundaries drawn from their own
coordinates - the shapes, their relative positions and their status colours,
with a scale bar. It cannot show the ground, and it says so, because a map that
looks like imagery and is not would be worse than a plain drawing.

NO NETWORK. AT ALL.
-------------------
No CDN, no webfont, no tile server, no analytics. A test asserts the generated
file contains no external reference. This matters beyond convenience: a page
that phones home each time it is opened tells a third party which tenancy
somebody is looking at.
"""

from __future__ import annotations

import html
import json
import math
from datetime import datetime, timezone
from typing import Optional

import crops as C
import vocab as V


# Must match farmer_app/view.py exactly; a test pins them together so the
# printed sheet and the screen cannot disagree about what a colour means.
STATUS_HEX = {
    "attention": "#C83C2D",
    "watch": "#EBA537",
    "ok": "#46965F",
    "unmeasured": "#828287",
}

# A shape as well as a colour. On the monochrome photocopy that reaches the
# meeting, red and green are the same grey.
STATUS_MARK = {"attention": "▲", "watch": "●", "ok": "■", "unmeasured": "□"}

STATUS_AR = {"attention": "تحتاج انتباهًا", "watch": "للمراقبة",
             "ok": "سليمة", "unmeasured": "لم تُقَس"}
STATUS_EN = {"attention": "needs attention", "watch": "watch",
             "ok": "ok", "unmeasured": "not measured"}


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _status_of(rec: dict, farm_vigours=None) -> str:
    v = ((rec or {}).get("crop_health") or {}).get("readings", {}).get(
        "vigour", {})
    if v.get("status") != "OK" or v.get("value") is None:
        return "unmeasured"
    thr = v.get("threshold")
    if thr is not None and v["value"] < thr:
        return "attention"
    if farm_vigours and len(farm_vigours) >= 3:
        ordered = sorted(farm_vigours)
        if v["value"] <= ordered[max(0, len(ordered) // 3 - 1)]:
            return "watch"
    return "ok"


def _vigour(rec: dict) -> Optional[float]:
    v = ((rec or {}).get("crop_health") or {}).get("readings", {}).get(
        "vigour", {})
    return v.get("value") if v.get("status") == "OK" else None


# ==============================================================================
# THE MAP
# ==============================================================================

def svg_map(features: list, statuses: dict, width: int = 640,
            height: int = 420, ar: bool = True) -> str:
    """
    The field boundaries, drawn from their own coordinates.

    Equirectangular with a cosine correction on longitude - at a farm's extent
    the error is far below the width of the pen. Not a projection anybody
    should measure from, and the caption says the scale bar is approximate.
    """
    polys = []
    for f in features or []:
        geom = f.get("geometry") or {}
        if geom.get("type") != "Polygon" or not geom.get("coordinates"):
            continue
        polys.append(((f.get("properties") or {}).get("name", ""),
                      geom["coordinates"][0]))
    if not polys:
        return ('<p class="none">%s</p>'
                % ("لا حدود حقول في هذا التقرير، فلا خريطة." if ar
                   else "No field boundaries in this report, so no map."))

    pts = [p for _n, ring in polys for p in ring]
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    lat0 = math.radians(sum(lats) / len(lats))
    kx = math.cos(lat0)

    minx, maxx = min(lons) * kx, max(lons) * kx
    miny, maxy = min(lats), max(lats)
    pad = 0.06
    dx = (maxx - minx) or 1e-6
    dy = (maxy - miny) or 1e-6
    minx, maxx = minx - dx * pad, maxx + dx * pad
    miny, maxy = miny - dy * pad, maxy + dy * pad
    dx, dy = maxx - minx, maxy - miny
    scale = min(width / dx, height / dy)
    ox = (width - dx * scale) / 2.0
    oy = (height - dy * scale) / 2.0

    def xy(p):
        x = ox + (p[0] * kx - minx) * scale
        y = height - (oy + (p[1] - miny) * scale)     # SVG y grows downward
        return f"{x:.1f},{y:.1f}"

    parts = []
    for name, ring in polys:
        st = statuses.get(name, "unmeasured")
        pts_s = " ".join(xy(p) for p in ring)
        cx = sum(float(xy(p).split(",")[0]) for p in ring) / len(ring)
        cy = sum(float(xy(p).split(",")[1]) for p in ring) / len(ring)
        parts.append(
            f'<polygon points="{pts_s}" fill="{STATUS_HEX[st]}" '
            f'fill-opacity="0.55" stroke="#333" stroke-width="1"/>'
            f'<text x="{cx:.1f}" y="{cy:.1f}" class="lbl">'
            f'{STATUS_MARK[st]} {_e(name)}</text>')

    # Scale bar: a round number of metres near a fifth of the width.
    m_per_deg = 111320.0
    span_m = dx / kx * m_per_deg if kx else dx * m_per_deg
    target = span_m / 5.0
    step = 10 ** math.floor(math.log10(max(target, 1)))
    bar_m = max(step, round(target / step) * step)
    bar_px = bar_m / span_m * (dx * scale)
    parts.append(
        f'<g transform="translate(14,{height - 18})">'
        f'<line x1="0" y1="0" x2="{bar_px:.1f}" y2="0" stroke="#111" '
        f'stroke-width="2"/>'
        f'<text x="0" y="-5" class="scale">~{int(bar_m)} m</text></g>')

    caption = ("رسم للحدود من إحداثياتها — ليس صورة قمر ولا يُظهر الأرض."
               if ar else
               "The boundaries drawn from their coordinates - not imagery, and "
               "it does not show the ground.")
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" '
            f'preserveAspectRatio="xMidYMid meet" role="img">'
            f'{"".join(parts)}</svg><p class="cap">{caption}</p>')


# ==============================================================================
# THE PAGE
# ==============================================================================

CSS = """
:root { --ink:#1C2321; --soft:#5A6560; --line:#E3DED3; --paper:#fff; }
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:#FBFAF7; color:var(--ink);
  font-family:'Segoe UI','SF Arabic','Noto Sans Arabic','Noto Naskh Arabic',
  Tahoma, system-ui, sans-serif; font-size:14px; line-height:1.55; }
body.rtl { direction:rtl; text-align:right; }
.wrap { max-width:900px; margin:0 auto; }
h1 { font-size:1.4rem; margin:0 0 .2rem; }
h2 { font-size:1.05rem; margin:1.6rem 0 .4rem;
     border-bottom:1px solid var(--line); padding-bottom:.25rem; }
.sub { color:var(--soft); font-size:.85rem; margin:0 0 1rem; }
.tags { margin:.5rem 0 1rem; }
.tag { display:inline-block; border:1px solid var(--line); border-radius:999px;
  padding:.1rem .6rem; font-size:.75rem; color:var(--soft); margin-inline-end:.35rem;
  background:#fff; }
.tag.demo { border-color:#EBA537; background:#FDF6E7; color:#8A5B08;
  font-weight:700; }
table { width:100%; border-collapse:collapse; font-size:.82rem;
  background:var(--paper); }
th { text-align:start; font-size:.7rem; text-transform:uppercase;
  letter-spacing:.05em; color:var(--soft); border-bottom:1px solid var(--line);
  padding:.35rem .45rem; }
td { padding:.35rem .45rem; border-bottom:1px solid #F2EFE8; vertical-align:top; }
td.v { font-variant-numeric:tabular-nums; font-weight:600; }
td.meta { color:var(--soft); font-size:.76rem; }
tr.na td { color:var(--soft); font-style:italic; }
td.below { color:#C83C2D; font-weight:700; }
.field { border:1px solid var(--line); border-radius:10px; background:#fff;
  padding:.8rem 1rem; margin-bottom:.8rem; }
.field h3 { margin:0 0 .3rem; font-size:1rem; }
.chip { display:inline-block; padding:.1rem .5rem; border-radius:999px;
  color:#fff; font-size:.72rem; font-weight:700; }
.note { border-inline-start:3px solid var(--line); padding:.3rem .7rem;
  color:var(--soft); font-size:.8rem; margin:.5rem 0; }
.note.warn { border-inline-start-color:#EBA537; }
.note.stop { border-inline-start-color:#C83C2D; }
.map { background:#fff; border:1px solid var(--line); border-radius:10px;
  padding:.6rem; overflow-x:auto; }
svg { display:block; max-width:100%; height:auto; }
/* A six-column table on a 390 px phone overflows the page, and a page that
   scrolls sideways is a page whose right-hand column nobody reads. The table
   scrolls inside its own box; the page never does. */
.tblwrap { overflow-x:auto; -webkit-overflow-scrolling:touch; }
@media (max-width:560px) {
  body { padding:12px; font-size:13px; }
  .field { padding:.6rem .7rem; }
  h1 { font-size:1.2rem; }
}
.lbl { font-size:10px; fill:#111; text-anchor:middle; }
.scale { font-size:10px; fill:#111; }
.cap { font-size:.75rem; color:var(--soft); margin:.35rem 0 0; }
.none { color:var(--soft); font-style:italic; }
ul.limits { padding-inline-start:1.1rem; }
ul.limits li { font-size:.8rem; color:var(--soft); margin-bottom:.25rem; }
footer { margin-top:2rem; padding-top:.6rem; border-top:1px solid var(--line);
  font-size:.72rem; color:var(--soft); }

@media print {
  /* Page breaks placed so a field never splits across two sheets, and the
     background printed so the status colours survive - but every status also
     carries a MARK, because on a monochrome photocopy red and green are the
     same grey. */
  body { background:#fff; padding:0; font-size:11pt; }
  .field { break-inside:avoid; page-break-inside:avoid; }
  h2 { break-after:avoid; page-break-after:avoid; }
  .noprint { display:none; }
  a[href]:after { content:""; }
  * { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
}
"""


def _variables_rows(rec: dict, ar: bool) -> list:
    """The measured variables, each with its sensor and the scale it was
    measured at - the two columns that make a figure checkable."""
    NA = "غير متاح" if ar else "not available"
    rows = []
    readings = (rec.get("crop_health") or {}).get("readings", {})
    for key, a, e in (("vigour", "النموّ (NDVI)", "Vigour (NDVI)"),
                      ("canopy_moisture", "رطوبة الغطاء (NDMI)",
                       "Canopy moisture (NDMI)"),
                      ("greenness", "الاخضرار (EVI)", "Greenness (EVI)")):
        r = readings.get(key, {})
        label = a if ar else e
        if r.get("status") == "OK":
            thr = r.get("threshold")
            below = thr is not None and r.get("value") is not None \
                and r["value"] < thr
            rows.append({"k": label, "v": f'{r.get("value"):.4f}',
                         "t": f"{thr:.4f}" if thr is not None else "—",
                         "r": (("دون العتبة" if ar else "BELOW threshold")
                               if below else
                               ("فوق العتبة" if ar else "above threshold")
                               if thr is not None else "—"),
                         "below": below, "s": r.get("sensor", ""),
                         "sc": f'{r.get("scale_m")} m' if r.get("scale_m") else "",
                         "na": False})
        else:
            rows.append({"k": label, "v": NA, "t": "—", "r": "—", "below": False,
                         "s": r.get("sensor", ""), "sc": "", "na": True,
                         "reason": r.get("reason", "")})

    th = rec.get("thermal_stress") or {}
    if th.get("status") == "OK":
        rows.append({"k": "حرارة السطح" if ar else "Surface temperature",
                     "v": f'{th.get("value")} °C',
                     "t": (f'{th.get("neighbourhood_c")} °C'
                           if th.get("neighbourhood_c") is not None else "—"),
                     "r": V.tr(V.THERMAL_READING, th.get("reading"), ar),
                     "below": False,
                     "s": th.get("sensor", ""), "sc": f'{th.get("scale_m")} m',
                     "na": False})

    rain = rec.get("rainfall") or {}
    for key, a, e in (("season_mm", "مطر الموسم", "Rainfall, season"),
                      ("last_14d_mm", "مطر آخر 14 يومًا",
                       "Rainfall, last 14 days")):
        v = rain.get(key)
        rows.append({"k": a if ar else e,
                     "v": f"{v} mm" if v is not None else NA, "t": "—",
                     "r": "—", "below": False, "s": rain.get("sensor", "CHIRPS"),
                     "sc": "5.5 km", "na": v is None})

    wr = rec.get("water_requirement") or {}
    if wr.get("etc_mm") is not None:
        rows.append({"k": ("الماء الذي احتاجه المحصول (ETc)" if ar
                           else "Crop water NEEDED (ETc)"),
                     "v": f'{wr["etc_mm"]} mm', "t": f'Kcb {wr.get("kcb")}',
                     "r": ("احتياج، لا ما وصل" if ar else "NEEDED, not received"),
                     "below": False, "s": "ERA5-Land + Sentinel-2",
                     "sc": "11 km", "na": False})

    clim = rec.get("climate") or {}
    if clim.get("heat_stress_days") is not None:
        rows.append({"k": "أيام الإجهاد الحراري" if ar else "Heat-stress days",
                     "v": (f'{round(clim["heat_stress_days"])} يومًا' if ar
                           else f'{round(clim["heat_stress_days"])} days'),
                     "t": (f'> {clim.get("heat_stress_threshold_c")} °C'),
                     "r": "—", "below": False, "s": "ERA5-Land", "sc": "11 km",
                     "na": False})

    svh = clim.get("season_vs_history") or {}
    if svh.get("this_season_mm") is not None:
        rows.append({"k": ("الموسم مقابل تاريخ الموقع" if ar
                           else "Season vs this site's history"),
                     "v": f'{svh["this_season_mm"]} mm', "t": "—",
                     "r": V.tr(V.SEASON_VERDICT, svh.get("verdict"), ar),
                     "below": False,
                     "s": "CHIRPS, 10 " + ("سنوات" if ar else "years"),
                     "sc": "5.5 km", "na": False})

    soil = rec.get("soil") or {}
    if soil.get("texture"):
        rows.append({"k": "قوام التربة" if ar else "Soil texture",
                     "v": V.tr(V.SOIL_TEXTURE, soil["texture"], ar),
                     "t": "—", "r": "—",
                     "below": False, "s": "OpenLandMap model", "sc": "250 m",
                     "na": False})
    return rows


def _field_block(rec: dict, status: str, ar: bool) -> str:
    name = _e(rec.get("name", ""))
    crop = rec.get("crop") or {}
    crop_label = crop.get("ar" if ar else "en") or (
        "غير محدّد" if ar else "unspecified")
    v = _vigour(rec)
    head = (f'<div class="field"><h3>{STATUS_MARK[status]} {name} '
            f'<span class="chip" style="background:{STATUS_HEX[status]}">'
            f'{(STATUS_AR if ar else STATUS_EN)[status]}</span></h3>'
            f'<p class="sub">{_e(crop_label)}'
            + (f' · NDVI {v:.3f}' if v is not None else "")
            + "</p>")

    rows = _variables_rows(rec, ar)
    hdr = (("المتغيّر", "القيمة", "مقارنًا بـ", "القراءة", "المستشعر", "قيس عند")
           if ar else
           ("Variable", "Value", "Compared with", "Reading", "Sensor",
            "Measured at"))
    body = "".join(
        f'<tr class="{"na" if r["na"] else ""}"'
        + (f' title="{_e(r.get("reason", ""))}"' if r.get("reason") else "")
        + f'><td>{_e(r["k"])}</td><td class="v">{_e(r["v"])}</td>'
        f'<td class="meta">{_e(r["t"])}</td>'
        f'<td class="{"below" if r["below"] else "meta"}">{_e(r["r"])}</td>'
        f'<td class="meta">{_e(r["s"])}</td>'
        f'<td class="meta">{_e(r["sc"])}</td></tr>' for r in rows)
    table = ('<div class="tblwrap"><table><thead><tr>'
             + "".join(f"<th>{_e(h)}</th>" for h in hdr)
             + f"</tr></thead><tbody>{body}</tbody></table></div>")

    # The disease ladder, if the report carries it, with its rung named.
    dz = rec.get("disease") or {}
    extra = ""
    if dz.get("headline"):
        extra += (f'<div class="note{" warn" if dz.get("claim_level") in ("ANOMALY", "REPORTED") else ""}">'
                  f'<b>{_e(dz.get("claim_level"))}</b> — '
                  f'{_e(dz.get("headline_ar" if ar else "headline"))}<br>'
                  f'{_e(dz.get("note_ar" if ar else "note"))}</div>')

    adv = rec.get("advisory" if ar else "advisory_en") or {}
    for item in adv.get("items", []):
        extra += f'<div class="note">{_e(item.get("text", ""))}</div>'

    return head + table + extra + "</div>"


def build(report: dict, field_fc: Optional[dict] = None,
          ar: bool = True, title: Optional[str] = None) -> str:
    """
    One self-contained HTML page. No network reference of any kind.

    A page that phones home each time it is opened tells a third party which
    tenancy somebody is looking at. That is a reason beyond convenience.
    """
    fields = report.get("fields", [])
    vigours = [v for v in (_vigour(f) for f in fields) if v is not None]
    statuses = {f.get("name", ""): _status_of(f, vigours) for f in fields}

    season = report.get("season", {})
    heading = title or ("تقرير المزرعة" if ar else "Farm report")
    counts = {k: sum(1 for s in statuses.values() if s == k)
              for k in STATUS_HEX}

    tags = [
        f'{"الموسم" if ar else "Season"} {season.get("start", "?")} → '
        f'{season.get("end", "?")}',
        f'{"المحصول" if ar else "Crop"} · '
        f'{C.label(report.get("crop"), ar)}',
        f'{len(fields)} {"حقلًا" if ar else "fields"}',
    ]
    tag_html = "".join(f'<span class="tag">{_e(t)}</span>' for t in tags)
    if report.get("note"):
        tag_html += (f'<span class="tag demo">'
                     f'{"عرض توضيحي" if ar else "DEMO"}</span>')

    legend = " ".join(
        f'<span class="tag">{STATUS_MARK[k]} '
        f'{(STATUS_AR if ar else STATUS_EN)[k]} — {counts[k]}</span>'
        for k in ("attention", "watch", "ok", "unmeasured"))

    body = [
        f'<div class="wrap"><h1>{_e(heading)}</h1>',
        f'<div class="tags">{tag_html}</div>',
        f'<h2>{"الخريطة" if ar else "Map"}</h2>',
        f'<div class="map">{svg_map((field_fc or {}).get("features", []), statuses, ar=ar)}</div>',
        f'<p class="cap">{legend}</p>',
        f'<h2>{"الحقول" if ar else "Fields"}</h2>',
    ]

    order = {"attention": 0, "watch": 1, "ok": 2, "unmeasured": 3}
    for rec in sorted(fields, key=lambda r: (
            order.get(statuses.get(r.get("name", ""), "unmeasured"), 9),
            _vigour(r) if _vigour(r) is not None else 9)):
        body.append(_field_block(rec, statuses.get(rec.get("name", ""),
                                                   "unmeasured"), ar))

    limits = ((report.get("limitations_ar") if ar else None)
              or report.get("limitations") or [])
    if limits:
        body.append(f'<h2>{"ما لا تدّعيه هذه الأداة" if ar else "What this tool does not claim"}</h2>')
        body.append('<ul class="limits">'
                    + "".join(f"<li>{_e(x)}</li>" for x in limits) + "</ul>")

    if report.get("note"):
        body.append(f'<div class="note stop">'
                    f'{_e(report.get("note_ar") if ar and report.get("note_ar") else report.get("note"))}'
                    f'</div>')

    body.append(
        '<footer>' + _e(
            ("وُلِّد هذا الملف مكتفيًا بذاته: لا يطلب شيئًا من الشبكة، ويُفتح "
             "بلا خادم. " if ar else
             "This file is self-contained: it requests nothing from the "
             "network and opens with no server. ")
            + f'{report.get("generated_utc", "")} · '
            + f'{datetime.now(timezone.utc).strftime("%Y-%m-%d")}')
        + '</footer></div>')

    return ("<!doctype html><html lang=\"" + ("ar" if ar else "en")
            + "\" dir=\"" + ("rtl" if ar else "ltr") + "\"><head>"
            "<meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,"
            "initial-scale=1\">"
            "<meta name=\"referrer\" content=\"no-referrer\">"
            f"<title>{_e(heading)}</title><style>{CSS}</style></head>"
            f"<body class=\"{'rtl' if ar else ''}\">{''.join(body)}</body></html>")


def write(path: str, report: dict, field_fc: Optional[dict] = None,
          ar: bool = True, title: Optional[str] = None) -> dict:
    doc = build(report, field_fc, ar, title)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return {"path": path, "bytes": len(doc.encode("utf-8")),
            "n_fields": len(report.get("fields", []))}


def external_references(doc: str) -> list:
    """Every reference to something outside this file.

    Used by a test rather than by the app: the promise "no network" is the kind
    that decays the first time somebody adds a convenient icon font.
    """
    import re
    bad = []
    for pattern in (r'https?://[^\s"\'<>]+', r'src\s*=\s*["\'](?!data:)[^"\']+',
                    r'@import[^;]+', r'<link[^>]+href'):
        bad += re.findall(pattern, doc, flags=re.IGNORECASE)
    return bad
