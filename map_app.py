"""
Scheme-manager MAP dashboard — the map-first view.

    streamlit run dashboard/map_app.py -- \
        --canals dashboard/newhalfa_canals.geojson \
        --command dashboard/newhalfa_command.geojson \
        --results docs/sample_results.json

Unlike the plain table view, this draws the actual canal NETWORK on a map:
each canal coloured by the selected metric (head-to-tail equity, canal water,
irrigated extent), command areas as translucent polygons, the main canal for
context, hover tooltips, and a click-to-inspect detail panel with the reach
profile, nutrition, climate, and the Arabic farmer card.

It joins geometry (the GeoJSON) with analysis (the engine's results JSON) by
canal name — exactly the way the real pipeline works: geometry in, engine
output out, joined for display. Uses pydeck (bundled with Streamlit) with a
Carto basemap, so it needs no map token and no extra install.

Every integrity guarantee still holds on the map: a NOT AVAILABLE canal is drawn
grey and labelled, a flagged canal is drawn bold red WITH its confidence
interval in the tooltip, an unreliable (low-head) gap is grey, and nothing is
attributed to any office or decision.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import streamlit as st
import pydeck as pdk

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import data as D
import farmer_channel as fc


# ----------------------------------------------------------------- loading ----

def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--canals", default="dashboard/newhalfa_canals.geojson")
    p.add_argument("--command", default="dashboard/newhalfa_command.geojson")
    p.add_argument("--results", default="docs/sample_results.json")
    known, _ = p.parse_known_args()
    return known


@st.cache_data(show_spinner=False)
def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------- colours ----

GREY = [140, 140, 140]
FLAG_RED = [214, 40, 40]


def _lerp(a, b, t):
    return [int(a[i] + (b[i] - a[i]) * t) for i in range(3)]


def canal_color(metric: str, rec: dict):
    """(rgb, width_px) for a canal under the chosen metric. Missing/unreliable
    values are grey — never a colour that reads as 'fine'."""
    eq = rec.get("head_tail_equity", {})
    if metric == "equity":
        if eq.get("status") != "OK" or not eq.get("gap_reliable", True):
            return GREY, 3
        if eq.get("flagged"):
            return FLAG_RED, 6
        gap = eq.get("head_tail_gap")
        if gap is None:
            return GREY, 3
        # green (no/negative gap) -> amber -> red (large gap)
        t = max(0.0, min(1.0, gap / 0.4))
        col = _lerp([60, 160, 90], [230, 170, 40], min(1.0, t * 2)) if t < 0.5 \
            else _lerp([230, 170, 40], [214, 40, 40], (t - 0.5) * 2)
        return col, 3 + int(4 * t)
    if metric == "water":
        cw = rec.get("canal_water", {})
        if cw.get("status") != "OK" or cw.get("value") is None:
            return GREY, 3
        t = max(0.0, min(1.0, cw["value"] / 0.5))
        return _lerp([210, 225, 245], [20, 90, 200], t), 4
    if metric == "extent":
        ext = rec.get("irrigated_extent", {})
        if ext.get("status") != "OK" or ext.get("value") is None:
            return GREY, 3
        note = (ext.get("provenance") or {}).get("notes", "")
        if "WEAK SPLIT" in note:
            return [170, 150, 90], 3          # muted: unreliable
        t = max(0.0, min(1.0, ext["value"]))
        return _lerp([225, 235, 210], [40, 140, 50], t), 4
    return GREY, 3


# ----------------------------------------------------------------- app ---------

def main():
    st.set_page_config(page_title="مراقبة الرِيّ — السودان", layout="wide")
    a = _args()
    canals = _load(a.canals)
    results = _load(a.results) if os.path.exists(a.results) else {"canals": []}
    command = _load(a.command) if os.path.exists(a.command) else {"features": []}

    rec_by_name = {c.get("name"): c for c in results.get("canals", [])}

    st.title("منصّة مراقبة الرِيّ والزراعة — السودان")
    st.caption("Sudan Irrigation & Agriculture Monitor · عرض المدير — طبقة الشبكة أولاً. "
               "كل رقم يصف حالة مقيسة ولا يَنسب شيئًا لأي مكتب أو قرار.")

    if results.get("note"):
        st.info(results["note"])

    rows = D.canal_rows(results)
    season = results.get("season", {})
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("عدد الترع", len(rec_by_name))
    k2.metric("مُعلَّمة للمراجعة 🚩", D.flagged_count(rows))
    k3.metric("الموسم", f"{season.get('start','?')} → {season.get('end','?')}")
    k4.metric("المحصول", results.get("crop", "—"))

    metric = st.radio(
        "طبقة الخريطة", ["equity", "water", "extent"],
        format_func=lambda m: {"equity": "فجوة الرأس/الذيل (العدالة)",
                               "water": "ماء الترعة (رادار)",
                               "extent": "المساحة المرويّة"}[m],
        horizontal=True)

    # ---- build map layers ----
    paths, polys = [], []
    minor_features = [f for f in canals.get("features", [])
                      if f.get("properties", {}).get("role") != "main"]
    main_features = [f for f in canals.get("features", [])
                     if f.get("properties", {}).get("role") == "main"]

    for f in minor_features:
        name = f["properties"].get("name")
        rec = rec_by_name.get(name, {})
        col, w = canal_color(metric, rec)
        eq = rec.get("head_tail_equity", {})
        gap = eq.get("head_tail_gap")
        ci = eq.get("head_tail_gap_ci95")
        cw = rec.get("canal_water", {})
        paths.append({
            "path": f["geometry"]["coordinates"],
            "name": name,
            "color": col, "width": w,
            "gap": ("—" if (gap is None or not eq.get("gap_reliable", True))
                    else f"{gap*100:.0f}%"),
            "ci": (f"{ci[0]*100:.0f}%…{ci[1]*100:.0f}%" if ci and ci[0] is not None else "—"),
            "flagged": "نعم 🚩" if eq.get("flagged") else "لا",
            "water": (f"{cw.get('value')}" if cw.get("status") == "OK" else "غير متاح"),
        })

    for f in command.get("features", []):
        name = f["properties"].get("canal")
        rec = rec_by_name.get(name, {})
        col, _ = canal_color(metric, rec)
        rings = f["geometry"]["coordinates"]
        polys.append({"polygon": rings[0], "name": name,
                      "color": col + [55]})

    main_paths = [{"path": f["geometry"]["coordinates"], "name": "الترعة الرئيسية"}
                  for f in main_features]

    layers = [
        pdk.Layer("PolygonLayer", polys, get_polygon="polygon",
                  get_fill_color="color", get_line_color=[90, 90, 90],
                  line_width_min_pixels=1, pickable=True, stroked=True, filled=True),
        pdk.Layer("PathLayer", main_paths, get_path="path",
                  get_color=[60, 60, 60], get_width=7, width_min_pixels=3,
                  pickable=False),
        pdk.Layer("PathLayer", paths, get_path="path", get_color="color",
                  get_width="width", width_min_pixels=3, width_scale=1,
                  pickable=True, cap_rounded=True, joint_rounded=True),
    ]

    # centre on the geometry
    all_pts = [pt for f in minor_features for pt in f["geometry"]["coordinates"]]
    if all_pts:
        clon = sum(p[0] for p in all_pts) / len(all_pts)
        clat = sum(p[1] for p in all_pts) / len(all_pts)
    else:
        clon, clat = 35.7, 15.35
    view = pdk.ViewState(longitude=clon, latitude=clat, zoom=10.5, pitch=0)

    tooltip = {
        "html": "<b>{name}</b><br/>فجوة الرأس/الذيل: {gap} (فاصل {ci})<br/>"
                "مُعلَّمة: {flagged}<br/>ماء الترعة: {water}",
        "style": {"backgroundColor": "#111", "color": "white", "fontSize": "12px"},
    }
    deck = pdk.Deck(layers=layers, initial_view_state=view, tooltip=tooltip,
                    map_provider="carto", map_style="road")

    st.subheader("خريطة شبكة الترع")
    st.caption("لون الترعة حسب الطبقة المختارة. الأحمر العريض = مُعلَّمة (الحدّ الأدنى "
               "لفاصل الثقة يتجاوز عتبة المراجعة). الرمادي = غير متاح أو غير موثوق. "
               "الخطّ الداكن العريض = الترعة الرئيسية.")
    st.pydeck_chart(deck, use_container_width=True)

    # ---- legend ----
    if metric == "equity":
        st.caption("🟩 فجوة صغيرة/سالبة  →  🟧 متوسطة  →  🟥 كبيرة/مُعلَّمة   |   ⬜ غير موثوقة")
    elif metric == "water":
        st.caption("أزرق فاتح → غامق = نسبة ماء أعلى في الترعة   |   ⬜ غير متاح")
    else:
        st.caption("أخضر أغمق = نسبة مساحة مرويّة أعلى   |   بنّي باهت = فصل ضعيف (غير موثوق)")

    # ---- detail panel ----
    st.subheader("تفاصيل الترعة")
    names = [f["properties"]["name"] for f in minor_features]
    sel = st.selectbox("اختر ترعة", names,
                       index=next((i for i, n in enumerate(names)
                                   if rec_by_name.get(n, {}).get("head_tail_equity", {}).get("flagged")), 0))
    _render_detail(rec_by_name.get(sel, {}))


def _render_detail(canal: dict):
    if not canal:
        st.write("لا توجد نتائج لهذه الترعة.")
        return
    left, right = st.columns([3, 2])
    with left:
        st.markdown("**مقطع الرأس إلى الذيل**")
        rs = D.reach_series(canal)
        if not rs["available"]:
            st.write(f"غير متاح — {rs['reason']}")
        else:
            st.line_chart({"NDVI على طول الترعة (0=رأس، 1=ذيل)":
                           dict(zip([f"{p:.2f}" for p in rs["positions"]], rs["ndvi"]))})
            if rs["gap"] is not None:
                ci = rs["ci"]
                ci_txt = (f" (فاصل ٩٥٪ {100*ci[0]:.0f}%…{100*ci[1]:.0f}%)"
                          if ci and ci[0] is not None else "")
                st.write(f"الفجوة رأس→ذيل: **{100*rs['gap']:.0f}%**{ci_txt}، R² {rs['r2']}.")
            st.caption(rs["caveat"])

        st.markdown("**بطاقة المزارع**")
        card = fc.farmer_card(canal, reach_position=1.0, lang="ar")
        st.info(card["text"])

    with right:
        st.markdown("**ماء الترعة (Sentinel-1)**")
        cw = canal.get("canal_water", {})
        st.write(f"نسبة الماء: {cw['value']}" if cw.get("status") == "OK"
                 else f"غير متاح — {cw.get('reason','')}")
        for line in D.provenance_lines(cw):
            st.caption(line)

        st.markdown("**التغذية**")
        n = D.nutrition_summary(canal)
        st.write(n["headline"] if n["available"] else f"غير متاح — {n['reason']}")
        if n["available"]:
            st.caption(n["caveat"])

        st.markdown("**المناخ**")
        clim = canal.get("climate", {})
        svh = (clim or {}).get("season_vs_history")
        if svh:
            st.write(f"المطر: {svh.get('verdict','—')} "
                     f"({svh.get('this_season_mm','—')} مم)")
        ds = (clim or {}).get("dry_spells")
        if ds:
            st.write(f"أطول انقطاع مطر: {ds.get('longest_dry_spell_days','—')} يومًا")

        st.markdown("**المساحة المرويّة**")
        ext = canal.get("irrigated_extent", {})
        st.write(f"نسبة مزروعة: {ext['value']}" if ext.get("status") == "OK"
                 else f"غير متاح — {ext.get('reason','')}")
        for line in D.provenance_lines(ext):
            st.caption(line)


if __name__ == "__main__":
    main()
