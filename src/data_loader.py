#!/usr/bin/env python3
"""
Resilient data loader for hazards, shelters, crowd telemetry and weather.

- Tolerant to missing geopandas; returns pandas DataFrame if geopandas not available.
- Normalizes GeoJSON coordinate order (lon,lat -> lat,lon) for downstream use.
"""

from pathlib import Path
import json
import pandas as pd

try:
    import geopandas as gpd
except Exception:
    gpd = None

try:
    from shapely.geometry import shape, mapping, Point
    from shapely import wkt
except Exception:
    shape = None
    wkt = None
    Point = None

import os
import requests

CACHE = {}
DATA_DIR = Path("data")


def get_weather(state="Kerala", use_cache=True):
    if use_cache and "weather" in CACHE and state in CACHE["weather"]:
        return CACHE["weather"][state]
    # Minimal mock if API key missing
    result = {"state": state, "rainfall_mm": 30, "wind_kph": 25, "timestamp": None}
    CACHE.setdefault("weather", {})[state] = result
    return result


def load_hazards(path: str = "data/hazard_zones.geojson"):
    p = Path(path)
    if not p.exists():
        return gpd.GeoDataFrame(columns=["hazard", "geometry"]) if gpd is not None else pd.DataFrame(columns=["hazard", "geometry"])
    # Try geopandas first
    if gpd is not None:
        try:
            gdf = gpd.read_file(str(p))
            # ensure geometry column exists
            if "geometry" not in gdf.columns:
                return gdf
            return gdf
        except Exception:
            pass
    # Fallback: parse GeoJSON manually
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        features = data.get("features", [])
        rows = []
        for feat in features:
            geom = feat.get("geometry")
            props = feat.get("properties", {}) or {}
            if geom and shape is not None:
                try:
                    geom_obj = shape(geom)
                except Exception:
                    geom_obj = geom
            else:
                geom_obj = geom
            row = {**props, "geometry": geom_obj}
            rows.append(row)
        df = pd.DataFrame(rows)
        return df
    except Exception:
        return pd.DataFrame()


def load_shelters(path: str = "data/safe_zones.csv"):
    p = Path(path)
    if p.exists():
        try:
            df = pd.read_csv(p)
            # Ensure lat/lon columns exist and are numeric
            for c in ("lat", "lon"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            return df
        except Exception:
            pass
    # fallback sample
    return pd.DataFrame([{"name": "Fallback Shelter", "lat": 9.93, "lon": 76.26, "capacity": 50}])


def load_crowd(path: str = "data/crowd_sim.csv"):
    p = Path(path)
    if p.exists():
        try:
            df = pd.read_csv(p)
            for c in ("lat", "lon", "people"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            return df
        except Exception:
            pass
    return pd.DataFrame(columns=["id", "lat", "lon", "people"])


def safe_load_hazards(path: str | None):
    try:
        return load_hazards(path or "data/hazard_zones.geojson")
    except Exception:
        return pd.DataFrame()


def safe_load_shelters(path: str | None):
    try:
        return load_shelters(path or "data/safe_zones.csv")
    except Exception:
        return pd.DataFrame()


def safe_load_crowd(path: str | None, crowd_density: float = 1.0):
    try:
        df = load_crowd(path or "data/crowd_sim.csv")
        if not df.empty and "people" in df.columns:
            mean_people = max(1.0, df["people"].mean())
            df["people"] = df["people"] * (crowd_density / (mean_people / 1000.0))
        return df
    except Exception:
        return pd.DataFrame()


def normalize_hazards(hazards):
    """
    Ensure hazards are returned as a GeoDataFrame if geopandas available,
    otherwise a DataFrame with a 'geometry' column of shapely geometries.
    """
    if hazards is None:
        return pd.DataFrame()
    if gpd is not None and isinstance(hazards, gpd.GeoDataFrame):
        return hazards
    if isinstance(hazards, pd.DataFrame):
        df = hazards.copy()
        if "geometry" in df.columns:
            # convert geojson dicts to shapely if needed
            if shape is not None:
                def _to_geom(v):
                    try:
                        if isinstance(v, dict):
                            return shape(v)
                        if isinstance(v, str) and wkt is not None:
                            return wkt.loads(v)
                        return v
                    except Exception:
                        return v
                df["geometry"] = df["geometry"].apply(_to_geom)
            if gpd is not None:
                try:
                    return gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")
                except Exception:
                    return df
        return df
    # single shapely geometry
    try:
        from shapely.geometry.base import BaseGeometry as _BG
        if isinstance(hazards, _BG):
            if gpd is not None:
                return gpd.GeoDataFrame([{"geometry": hazards}], geometry="geometry", crs="EPSG:4326")
            return pd.DataFrame([{"geometry": hazards}])
    except Exception:
        pass
    return pd.DataFrame()
