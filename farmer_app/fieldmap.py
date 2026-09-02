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
import math
from typing import Optional

import folium
from branca.element import MacroElement, Template
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


class MetricScale(MacroElement):
    """
    A scale bar in metres and kilometres, and nothing else.

    folium's `control_scale=True` builds Leaflet's default control, which draws
    TWO lines - metric above imperial - and exposes no way to turn the second
    one off. The bar read "3 km" over "2 mi". A mile is not a unit anybody in
    Sudan measures a field with, and the two sat one under the other, so the
    reader had to decide every time which of the numbers was theirs. Removing
    it does not simplify the map; it removes a decision that should never have
    been put to them.

    Written as a MacroElement rather than injected into the root script,
    because streamlit-folium rebuilds the map from its properties and a script
    added that way does not survive the trip - the first attempt removed the
    scale bar altogether instead of fixing it, which is worse than the defect.
    A MacroElement renders into the map's own script block and does survive.
    """

    _template = Template("""
        {% macro script(this, kwargs) %}
        L.control.scale({
            imperial: false, metric: true, maxWidth: 140, position: 'bottomleft'
        }).addTo({{ this._parent.get_name() }});
        {% endmacro %}
    """)

    def __init__(self):
        super().__init__()
        self._name = "MetricScale"


def _hex(rgba) -> str:
    return "#%02x%02x%02x" % tuple(rgba[:3])


def centre_and_zoom(features, width_px: int = 550, height_px: int = 520):
    """
    Frame the map on the fields that exist, rather than guessing a zoom.

    A farm spread over ten kilometres has a mean position with no field
    anywhere near it, so opening at a fixed zoom around that mean showed bare
    ground with every polygon just off the edge - which is indistinguishable,
    to the person looking, from a map that failed to draw them. That is the
    complaint "the field shapes do not show", and it survives having drawn the
    shapes correctly.

    folium's own fit_bounds is not used: streamlit-folium rebuilds the map from
    its properties and the fitBounds call does not survive the trip. Computing
    the zoom here is deterministic, testable without a browser, and cannot be
    quietly dropped by a component upgrade.
    """
    pts = [p for f in features for p in f.get("polygon", [])]
    if not pts:
        return None, None
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    centre = ((min(lats) + max(lats)) / 2.0, (min(lons) + max(lons)) / 2.0)

    # Web Mercator: a tile is 256 px and covers 360 degrees of longitude at
    # zoom 0. A single field has a span of zero, so the spans are floored to
    # something small enough to give a close view without dividing by nought.
    lon_span = max(max(lons) - min(lons), 1e-4)
    lat_span = max(max(lats) - min(lats), 1e-4)
    z_lon = math.log2(360.0 * width_px / (256.0 * lon_span))
    z_lat = math.log2(180.0 * height_px / (256.0 * lat_span))
    zoom = int(max(2, min(18, math.floor(min(z_lon, z_lat)))))
    return centre, zoom


def build_map(features: list, centre: tuple, zoom: int = 14,
              drawing: bool = True, highlight=None) -> folium.Map:
    """
    Satellite map with the analysed fields drawn on it and, optionally, the
    tools to draw more.

    `features` are the joined polygons from view.map_features - each already
    carrying the colour that says what was measured and what was not.

    `highlight` is the set of names the current search matched. Fields outside
    it are DIMMED, not removed. Removing them would mean a filter could make a
    field vanish from the picture of the farm, and a farmer scrolling past an
    empty patch of imagery has no way to tell a field that was filtered out
    from one the tool never had.
    """
    fitted, fitted_zoom = centre_and_zoom(features)
    lat, lon = fitted or centre
    # control_scale=False, then the scale bar is added by hand below.
    # folium's flag builds Leaflet's default control, which draws TWO lines -
    # metric and imperial - and there is no way to turn the second off through
    # it. The bar read "3 km" above "2 mi".
    m = folium.Map(location=[lat, lon], zoom_start=fitted_zoom or zoom,
                   tiles=None, control_scale=False)

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
        on = highlight is None or f["name"] in highlight
        folium.Polygon(
            # folium wants (lat, lon); GeoJSON is (lon, lat).
            locations=[(p[1], p[0]) for p in f["polygon"]],
            color="#ffffff" if on else "#C9C4B8",
            weight=2 if on else 1, fill=True, fill_color=colour,
            fill_opacity=0.45 if on else 0.10,
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
        # METRIC ON BOTH LINES.
        #
        # leaflet-measure defaults its second line to miles and acres, so
        # measuring a drawn boundary answered "43 hectares / 106 acres". Sudan
        # is metric, and Sudanese agriculture measures land in feddan and
        # hectare; an acre is not a unit anybody here converts from. A second
        # line in units the reader does not use is not extra information, it is
        # a number they have to ignore beside the one they wanted.
        MeasureControl(primary_length_unit="meters",
                       secondary_length_unit="kilometers",
                       primary_area_unit="hectares",
                       secondary_area_unit="sqmeters",
                       position="bottomleft").add_to(m)

    Geocoder(collapsed=False, position="topright",
             add_marker=False, placeholder="ابحث عن مكان · Search a place"
             ).add_to(m)
    MiniMap(toggle_display=True, position="bottomright").add_to(m)
    folium.LayerControl(collapsed=True, position="topright").add_to(m)

    m.add_child(MetricScale())
    return m


def render(features: list, centre: tuple, key: str = "fieldmap",
           height: int = 520, drawing: bool = True, highlight=None) -> dict:
    """Draw the map and return whatever streamlit-folium reports back: the
    shapes drawn this run, and where the last click landed.

    `last_object_clicked` is returned so a click on a field can select it. The
    click is resolved to a field by point-in-polygon rather than by matching
    the tooltip text, because tooltip text is display copy - it gets reworded,
    translated and truncated, and a selector built on it breaks silently in
    whichever language nobody tested."""
    m = build_map(features, centre, drawing=drawing, highlight=highlight)
    return st_folium(m, key=key, height=height,
                     use_container_width=True,
                     returned_objects=["all_drawings", "last_active_drawing",
                                       "last_object_clicked"])


def last_drawn_polygon(state) -> Optional[dict]:
    """The most recent drawn shape, as a geometry to select fields with.

    Only the LAST shape counts. Two shapes drawn at once would need a union
    rule, and every choice of rule is a surprise to somebody - so the tool
    takes the newest one and the caption says so."""
    drawings = (state or {}).get("all_drawings") or []
    for shape in reversed(drawings):
        geom = (shape or {}).get("geometry") or {}
        if geom.get("type") == "Polygon" and geom.get("coordinates"):
            return geom
    return None


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
