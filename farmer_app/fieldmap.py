"""
The working map: satellite imagery, a polygon tool, and a place search.

WHAT WAS WRONG WITH THE FIRST MAP
---------------------------------
It drew the fields correctly and was useless for the job. Three reasons, all of
them the same mistake in different clothes - it was built to DISPLAY a result
rather than to be WORKED ON.

  1. The basemap was a road map. A farmer looking at a pale rectangle over grey
     streets cannot tell whether the outline sits on their field, and checking
     that is the first thing anyone does with a farm map. Satellite imagery is
     not decoration here; it is the reference the drawing is checked against.

  2. Fields could only arrive as a GeoJSON file path typed into a sidebar. That
     asks a farmer to produce a file format to describe land they can see out of
     the window. The polygon tool replaces it: draw the boundary on the imagery,
     and the file is written for you.

  3. There was no way to get to your own land. The map opened where the report
     happened to be and offered no search, so anyone starting fresh had to know
     their coordinates.

WHY FOLIUM AND NOT PYDECK
-------------------------
pydeck draws beautifully and cannot be drawn ON. Leaflet's Draw and Geocoder
plugins, through folium, give an editable polygon layer and a place search that
return their result to Python. Both ship in the folium and streamlit-folium
already in this project's requirements.

THE ONE THING THE IMAGERY DOES NOT CHANGE
-----------------------------------------
Esri's World Imagery is a mosaic of scenes from different dates. It shows where
a field IS; it says nothing about how the crop is doing THIS season, and the
date of any given tile is not published per pixel. Nobody should read the
greenness of the basemap as a measurement - the measurements are the coloured
overlay and the numbers beside it.
"""

from __future__ import annotations

import json
from typing import Optional

import folium
from folium.plugins import Draw, Geocoder, MeasureControl, MiniMap
from streamlit_folium import st_folium

import view as D


# Esri World Imagery: high resolution, global, and free to use with attribution.
# No token, which matters for a tool that must run from a field office.
ESRI_IMAGERY = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}")
ESRI_ATTR = "Esri, Maxar, Earthstar Geographics"

# A label layer over the imagery, so towns and canals are findable without
# losing the ground.
ESRI_LABELS = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}")


def _hex(rgba) -> str:
    return "#%02x%02x%02x" % tuple(rgba[:3])


def build_map(features: list, centre: tuple, zoom: int = 14,
              drawing: bool = True) -> folium.Map:
    """
    Satellite map with the analysed fields drawn on it and, optionally, the
    tools to draw more.

    `features` are the joined polygons from view.map_features - each already
    carrying the colour that says what was measured and what was not.
    """
    lat, lon = centre
    m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles=None,
                   control_scale=True)

    # `show` matters here, not just layer order. Two base layers added without
    # it are BOTH active, and folium paints the later one on top - so adding a
    # street map for people who want it silently covered the satellite imagery
    # that is the whole point. The street map is offered and starts hidden.
    folium.TileLayer(tiles=ESRI_IMAGERY, attr=ESRI_ATTR, name="قمر · Satellite",
                     max_zoom=19, overlay=False, control=True,
                     show=True).add_to(m)
    folium.TileLayer(tiles=ESRI_LABELS, attr=ESRI_ATTR, name="أسماء · Places",
                     max_zoom=19, overlay=True, control=True,
                     opacity=0.9, show=True).add_to(m)
    folium.TileLayer("OpenStreetMap", name="شوارع · Street map", overlay=False,
                     control=True, max_zoom=19, show=False).add_to(m)

    for f in features:
        colour = _hex(f["colour"])
        folium.Polygon(
            # folium wants (lat, lon); GeoJSON is (lon, lat).
            locations=[(p[1], p[0]) for p in f["polygon"]],
            color="#ffffff", weight=2, fill=True, fill_color=colour,
            fill_opacity=0.45,
            tooltip=folium.Tooltip(
                f"<b>{f['name']}</b><br>{f['status']}<br>"
                f"NDVI {f['vigour_display']}<br>{f['why']}"),
        ).add_to(m)

    if drawing:
        # Polygon and rectangle only. A circle or a marker cannot be a field
        # boundary, and offering tools whose output the engine would reject is
        # a way of wasting somebody's afternoon.
        Draw(
            export=False,
            position="topleft",
            draw_options={"polyline": False, "circle": False,
                          "circlemarker": False, "marker": False,
                          "polygon": {"showArea": True,
                                      "shapeOptions": {"color": "#EBA537",
                                                       "weight": 3}},
                          "rectangle": {"showArea": True,
                                        "shapeOptions": {"color": "#EBA537",
                                                         "weight": 3}}},
            edit_options={"edit": True, "remove": True},
        ).add_to(m)
        MeasureControl(primary_length_unit="meters",
                       primary_area_unit="hectares",
                       position="bottomleft").add_to(m)

    Geocoder(collapsed=False, position="topright",
             add_marker=False, placeholder="ابحث عن مكان · Search a place"
             ).add_to(m)
    MiniMap(toggle_display=True, position="bottomright").add_to(m)
    folium.LayerControl(collapsed=True, position="topright").add_to(m)
    return m


def render(features: list, centre: tuple, key: str = "fieldmap",
           height: int = 520, drawing: bool = True) -> dict:
    """Draw the map and return whatever streamlit-folium reports back, which
    includes any shapes the user drew this run."""
    m = build_map(features, centre, drawing=drawing)
    return st_folium(m, key=key, height=height,
                     use_container_width=True,
                     returned_objects=["all_drawings", "last_active_drawing"])


# ==============================================================================
# TURNING DRAWN SHAPES INTO FIELDS
# ==============================================================================

def drawings_to_fields(state: Optional[dict], names: Optional[list] = None,
                       min_area_ha: float = 0.1) -> dict:
    """
    Convert what the user drew into an engine-ready field FeatureCollection.

    Rejects what the engine would reject anyway, here where the person can see
    it and fix it: anything that is not a polygon, and anything too small to
    contain a Sentinel-2 pixel meaningfully. Returning a file that fails three
    steps later, in a console, is not a kindness.
    """
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    import decision_logic as dl

    drawings = (state or {}).get("all_drawings") or []
    feats, rejected = [], []
    for i, shape in enumerate(drawings, 1):
        geom = (shape or {}).get("geometry") or {}
        if geom.get("type") != "Polygon":
            rejected.append({"index": i, "reason": "not a polygon",
                             "type": geom.get("type")})
            continue
        area = dl.geojson_area_m2(geom)
        if not area or area / 10000.0 < min_area_ha:
            rejected.append({
                "index": i,
                "reason": (f"{(area or 0) / 10000.0:.2f} ha is below the "
                           f"{min_area_ha} ha minimum - too small for a "
                           "10 m pixel to say anything useful")})
            continue
        name = (names[i - 1] if names and i <= len(names) and names[i - 1]
                else f"حقل {i}")
        feats.append({
            "type": "Feature",
            "properties": {"name": name, "area_ha": round(area / 10000.0, 2),
                           "source": "drawn"},
            "geometry": geom,
        })
    return {"type": "FeatureCollection", "features": feats,
            "rejected": rejected}


def save_fields(fc: dict, path: str) -> int:
    """Write the drawn fields where the engine can read them."""
    clean = {"type": "FeatureCollection", "features": fc.get("features", [])}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=1, ensure_ascii=False)
    return len(clean["features"])
