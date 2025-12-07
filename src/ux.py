#!/usr/bin/env python3
"""
Map UX helpers for CrowdShield.

- Robust folium rendering via streamlit-folium when available, otherwise HTML fallback.
- Tolerant GeoJSON/geometry handling and defensive route/marker drawing.
"""

from typing import Any, Iterable, List, Optional, Tuple
import logging

import folium

# Try to import streamlit-folium; fall back to streamlit.components.v1.html
try:
    from streamlit_folium import st_folium  # type: ignore
    ST_FOLIUM_AVAILABLE = True
except Exception:
    ST_FOLIUM_AVAILABLE = False
    try:
        from streamlit.components.v1 import html as st_html  # type: ignore
    except Exception:
        st_html = None  # final fallback; render_map will handle absence

logger = logging.getLogger("crowdshield.ux")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(h)
logger.setLevel(logging.INFO)


def create_base_map(center_point: Tuple[float, float] = (9.931233, 76.267304), zoom_start: int = 12) -> folium.Map:
    """
    Create a folium Map with a couple of base layers and controls.
    """
    try:
        m = folium.Map(location=center_point, zoom_start=zoom_start, tiles="OpenStreetMap", attr="© OpenStreetMap contributors")
        # Add a couple of optional tile layers (wrapped in try/except to be tolerant)
        try:
            folium.TileLayer(tiles="https://stamen-tiles.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png",
                             name="Stamen Terrain",
                             attr="Map tiles by Stamen Design, © OpenStreetMap contributors").add_to(m)
        except Exception:
            logger.debug("Stamen Terrain tiles not available")
        try:
            folium.TileLayer("CartoDB positron", name="CartoDB Positron").add_to(m)
        except Exception:
            logger.debug("CartoDB Positron tiles not available")
        folium.LayerControl().add_to(m)
        return m
    except Exception as e:
        logger.warning("Map creation error: %s", e)
        # Return a minimal map as fallback
        return folium.Map(location=center_point, zoom_start=zoom_start, tiles="OpenStreetMap")


def _normalize_geom_for_geojson(geom: Any):
    """
    Return a geo-interface compatible object for folium.GeoJson.
    Accepts shapely geometries, geojson dicts, or objects exposing __geo_interface__.
    """
    try:
        if hasattr(geom, "__geo_interface__"):
            return geom.__geo_interface__
        if isinstance(geom, dict) and "type" in geom and "coordinates" in geom:
            return geom
    except Exception:
        pass
    return None


def add_hazards_to_map(m: folium.Map, hazards: Any, i18n: Optional[dict] = None) -> None:
    """
    Add hazard polygons or points to the folium map.
    Accepts GeoDataFrame, DataFrame with geometry, list of shapely geometries, or geojson-like dicts.
    """
    if m is None or hazards is None:
        return
    try:
        # If it's a GeoDataFrame or DataFrame-like with iterrows
        if hasattr(hazards, "iterrows"):
            for _, row in hazards.iterrows():
                try:
                    label = row.get("name") or (i18n.get("hazard") if i18n else "Hazard")
                    risk_level = row.get("risk", "high")
                    color_map = {"low": "yellow", "medium": "orange", "high": "red", "critical": "darkred"}
                    color = color_map.get(str(risk_level).lower(), "red")
                    geom = row.get("geometry", None)
                    geoobj = _normalize_geom_for_geojson(geom)
                    if geoobj is not None:
                        folium.GeoJson(
                            geoobj,
                            name=label,
                            style_function=lambda feat, c=color: {"fillColor": c, "color": c, "weight": 2, "fillOpacity": 0.3},
                            tooltip=f"{label} ({risk_level})",
                            popup=folium.Popup(f"<b>{label}</b><br>Risk: {risk_level}", max_width=250)
                        ).add_to(m)
                        continue
                    # If geometry not geojson-able, try centroid marker
                    try:
                        # shapely geometry centroid fallback
                        c = getattr(geom, "centroid", None)
                        if c is not None and hasattr(c, "x") and hasattr(c, "y"):
                            folium.CircleMarker(location=(c.y, c.x), radius=6, color=color, fill=True, fill_color=color, fill_opacity=0.4, tooltip=f"{label} ({risk_level})").add_to(m)
                            continue
                    except Exception:
                        pass
                except Exception:
                    continue
            return
        # If hazards is an iterable of geometries or geojson dicts
        if isinstance(hazards, (list, tuple, set)):
            for item in hazards:
                try:
                    label = (i18n.get("hazard") if i18n else "Hazard")
                    geoobj = _normalize_geom_for_geojson(item)
                    if geoobj is not None:
                        folium.GeoJson(geoobj, name=label, style_function=lambda feat: {"fillColor": "#ff6666", "color": "#ff0000", "weight": 2, "fillOpacity": 0.3}).add_to(m)
                        continue
                    # shapely geometry fallback
                    c = getattr(item, "centroid", None)
                    if c is not None and hasattr(c, "x") and hasattr(c, "y"):
                        folium.CircleMarker(location=(c.y, c.x), radius=6, color="#ff0000", fill=True, fill_color="#ff0000", fill_opacity=0.4).add_to(m)
                except Exception:
                    continue
            return
        # Unknown type: try to treat as single geometry
        geoobj = _normalize_geom_for_geojson(hazards)
        if geoobj is not None:
            folium.GeoJson(geoobj, name=(i18n.get("hazard") if i18n else "Hazard")).add_to(m)
    except Exception as e:
        logger.warning("add_hazards_to_map error: %s", e)


def add_shelters_to_map(m: folium.Map, shelters: Any, i18n: Optional[dict] = None) -> None:
    """
    Add shelter markers from a DataFrame with lat/lon or a list of tuples.
    """
    if m is None or shelters is None:
        return
    try:
        if hasattr(shelters, "iterrows"):
            for _, r in shelters.iterrows():
                try:
                    name = str(r.get("name", "Shelter"))
                    capacity = r.get("capacity", "Unknown")
                    lat = r.get("lat", r.get("latitude", None))
                    lon = r.get("lon", r.get("longitude", None))
                    if lat is None or lon is None:
                        continue
                    lat = float(lat); lon = float(lon)
                    popup_text = f"<b>{name}</b><br>Capacity: {capacity}"
                    folium.CircleMarker(location=(lat, lon), radius=7, color="blue", fill=True, fill_color="blue", popup=folium.Popup(popup_text, max_width=250), tooltip=f"{name} ({capacity})").add_to(m)
                except Exception:
                    continue
            return
        # If list of tuples
        if isinstance(shelters, (list, tuple)):
            for s in shelters:
                try:
                    lat, lon = s[0], s[1]
                    name = s[2] if len(s) > 2 else "Shelter"
                    folium.Marker(location=(float(lat), float(lon)), icon=folium.Icon(color="green", icon="home"), tooltip=name).add_to(m)
                except Exception:
                    continue
    except Exception as e:
        logger.warning("add_shelters_to_map error: %s", e)


def _normalize_route_coords(route: Iterable[Any]) -> List[Tuple[float, float]]:
    """
    Convert a route (various formats) into a list of (lat, lon) tuples.
    Handles: (lat,lon), (lon,lat), dicts with lat/lon or x/y, and ORS-like coordinate dicts.
    """
    pts: List[Tuple[float, float]] = []
    for p in route:
        try:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                a, b = float(p[0]), float(p[1])
                # Heuristic: if first value is within latitude bounds, treat as (lat,lon)
                if -90 <= a <= 90 and -180 <= b <= 180:
                    pts.append((a, b))
                else:
                    pts.append((b, a))
                continue
            if isinstance(p, dict):
                if "lat" in p and "lon" in p:
                    pts.append((float(p["lat"]), float(p["lon"])))
                    continue
                if "y" in p and "x" in p:
                    pts.append((float(p["y"]), float(p["x"])))
                    continue
                if "coordinates" in p and isinstance(p["coordinates"], (list, tuple)) and len(p["coordinates"]) >= 2:
                    lon, lat = p["coordinates"][0], p["coordinates"][1]
                    pts.append((float(lat), float(lon)))
                    continue
        except Exception:
            continue
    return pts


def add_route_to_map(m: folium.Map, route: Any, i18n: Optional[dict] = None) -> None:
    """
    Draw a route on the map. Accepts a list of points in various formats.
    """
    if m is None or not route:
        return
    try:
        pts = _normalize_route_coords(route)
        if not pts or len(pts) < 2:
            return
        label = (i18n.get("route") if i18n else "Route")
        folium.PolyLine(locations=pts, color="green", weight=5, opacity=0.8, tooltip=label).add_to(m)
        folium.Marker(location=pts[0], icon=folium.Icon(color="green"), popup="Start").add_to(m)
        folium.Marker(location=pts[-1], icon=folium.Icon(color="red"), popup="End").add_to(m)
    except Exception as e:
        logger.warning("add_route_to_map error: %s", e)


def add_reports_to_map(m: folium.Map, reports: Any, i18n: Optional[dict] = None) -> None:
    """
    Add crowd reports or incident markers to the map.
    """
    if m is None or not reports:
        return
    try:
        for r in reports:
            try:
                lat = r.get("lat"); lon = r.get("lon")
                if lat is None or lon is None:
                    continue
                popup_html = f"<b>{r.get('type','Incident')}</b><br>Severity: {r.get('severity','?')}<br>{r.get('note','')}"
                folium.CircleMarker(location=(float(lat), float(lon)), radius=6, color="red", fill=True, fill_color="red", popup=folium.Popup(popup_html, max_width=250), tooltip=f"{r.get('type','Incident')} ({r.get('severity','?')})").add_to(m)
            except Exception:
                continue
    except Exception as e:
        logger.warning("add_reports_to_map error: %s", e)


def add_origin_to_map(m: folium.Map, origin: Tuple[float, float], i18n: Optional[dict] = None) -> None:
    """
    Add an origin marker with a visible icon and a small circle.
    """
    if m is None or origin is None:
        return
    try:
        label = (i18n.get("origin") if i18n else "Origin")
        lat, lon = float(origin[0]), float(origin[1])
        folium.CircleMarker(location=(lat, lon), radius=7, color="#2ECC71", fill=True, fill_color="#2ECC71", tooltip=label).add_to(m)
        folium.Marker(location=(lat, lon), icon=folium.Icon(color="green", icon="user"), popup=folium.Popup(f"<b>{label}</b>", max_width=150)).add_to(m)
    except Exception as e:
        logger.warning("add_origin_to_map error: %s", e)
        try:
            folium.Marker(location=origin, popup="Origin").add_to(m)
        except Exception:
            pass


def render_map(m: folium.Map, height: int = 500) -> None:
    """
    Render the folium map in Streamlit. Prefer streamlit-folium if available, otherwise use HTML fallback.
    """
    try:
        if ST_FOLIUM_AVAILABLE:
            # streamlit-folium handles embedding and interaction
            st_folium(m, width=None, height=height)
            return
        # Fallback: use HTML rendering via streamlit.components.v1.html if available
        if 'st_html' in globals() and st_html is not None:
            html_str = m._repr_html_()
            st_html(html_str, height=height)
            return
        # Last resort: print a warning and do nothing (caller should handle)
        logger.warning("No rendering backend available (install streamlit-folium or ensure streamlit.components.v1.html is importable).")
    except Exception as e:
        logger.warning("render_map error: %s", e)
        # Try HTML fallback once more
        try:
            if hasattr(m, "_repr_html_") and 'st_html' in globals() and st_html is not None:
                st_html(m._repr_html_(), height=height)
                return
        except Exception as e2:
            logger.warning("render_map HTML fallback failed: %s", e2)
            # Final fallback: create a minimal map and attempt to render it
            try:
                minimal = folium.Map(location=(9.931233, 76.267304), zoom_start=10)
                if ST_FOLIUM_AVAILABLE:
                    st_folium(minimal, width=None, height=height)
                elif 'st_html' in globals() and st_html is not None:
                    st_html(minimal._repr_html_(), height=height)
            except Exception as e3:
                logger.error("Final fallback map render failed: %s", e3)
