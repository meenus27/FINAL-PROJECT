#!/usr/bin/env python3
"""
Defensive routing helpers for CrowdShield.

- Works with OSMnx/networkx when available.
- Provides safe fallbacks (grid graph, dict graph) when libraries or data are missing.
- Robust edge-blocking that tolerates different hazard input types.
"""

from typing import List, Tuple, Optional, Any, Union
import math
import logging
import os

# Optional dependencies
try:
    import networkx as nx
except Exception:
    nx = None

try:
    import osmnx as ox
except Exception:
    ox = None

try:
    from shapely.geometry import Point, shape
    from shapely.geometry.base import BaseGeometry
except Exception:
    Point = None
    shape = None
    BaseGeometry = None

logger = logging.getLogger("crowdshield.routing")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(h)
logger.setLevel(logging.INFO)


def _haversine_km(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """Return great-circle distance between two (lat, lon) points in kilometers."""
    lat1, lon1 = p1
    lat2, lon2 = p2
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def load_graph(online: bool = True, center_point: Optional[Tuple[float, float]] = None, dist: int = 1500):
    """
    Load an OSMnx graph if available; otherwise return a simple grid graph or dict fallback.

    Parameters:
      - online: if True and osmnx available, attempt to fetch graph from OSM.
      - center_point: (lat, lon) center for online fetch or grid creation.
      - dist: search radius in meters for osmnx.

    Returns:
      - networkx.Graph or a dict fallback with 'nodes'/'edges' keys.
    """
    try:
        if ox is not None and online:
            center = center_point or (9.931233, 76.267304)
            try:
                # Use walking network by default for safety/evacuation scenarios
                G = ox.graph_from_point(center, dist=dist, network_type="walk")
                logger.info("Loaded OSMnx graph from point %s", center)
                return G
            except Exception as e:
                logger.warning("OSMnx graph_from_point failed: %s", e)
        # If OSMnx not available or online False, return a small grid graph (networkx) if possible
        return build_grid_graph(size=10, center_point=center_point)
    except Exception as e:
        logger.warning("load_graph fallback error: %s", e)
        return build_grid_graph(size=10, center_point=center_point)


def build_grid_graph(size: int = 10, center_point: Optional[Tuple[float, float]] = None):
    """
    Create a simple grid graph (networkx) for offline demo.

    - size: number of nodes per side (size x size)
    - center_point: optional (lat, lon) to offset coordinates; if None uses small numeric grid
    """
    try:
        if nx is None:
            # minimal dict fallback
            nodes = []
            edges = []
            for i in range(size):
                for j in range(size):
                    nodes.append(((i, j), {"pos": (i, j)}))
            return {"nodes": nodes, "edges": edges}
        # Build a 2D grid graph and attach approximate lat/lon attributes
        G = nx.grid_2d_graph(size, size)
        # If a center_point is provided, map grid indices to lat/lon around that center
        if center_point is not None:
            latc, lonc = center_point
            # choose a small spacing (approx degrees)
            spacing = 0.005  # ~500m depending on latitude; small demo spacing
            offset = size // 2
            for n in list(G.nodes):
                i, j = n
                lat = latc + (i - offset) * spacing
                lon = lonc + (j - offset) * spacing
                G.nodes[n]["y"] = float(lat)
                G.nodes[n]["x"] = float(lon)
                G.nodes[n]["coord"] = (float(lat), float(lon))
        else:
            for n in list(G.nodes):
                G.nodes[n]["y"] = float(n[0])
                G.nodes[n]["x"] = float(n[1])
                G.nodes[n]["coord"] = (float(n[0]), float(n[1]))
        # Add simple length attribute on edges
        for u, v in list(G.edges()):
            a = G.nodes[u]["coord"]
            b = G.nodes[v]["coord"]
            G.edges[u, v]["length"] = _haversine_km(a, b)
        return G
    except Exception as e:
        logger.warning("build_grid_graph error: %s", e)
        return {"nodes": [], "edges": []}


def _iter_hazard_geoms(hazard_polygons: Any):
    """
    Yield shapely geometry-like objects from various hazard inputs:
    - GeoDataFrame (iterates .geometry)
    - list of shapely geometries
    - list of geojson-like dicts
    """
    if hazard_polygons is None:
        return
    try:
        # GeoDataFrame or DataFrame with geometry column
        if hasattr(hazard_polygons, "geometry"):
            for g in hazard_polygons.geometry:
                yield g
            return
        # list-like
        if isinstance(hazard_polygons, (list, tuple, set)):
            for item in hazard_polygons:
                # shapely geometry
                if BaseGeometry is not None and isinstance(item, BaseGeometry):
                    yield item
                # geojson-like dict
                elif isinstance(item, dict) and "type" in item and "coordinates" in item:
                    if shape is not None:
                        try:
                            yield shape(item)
                        except Exception:
                            continue
                    else:
                        continue
                else:
                    # unknown, skip
                    continue
            return
    except Exception:
        return


def block_edges_by_hazards(G: Any, hazard_polygons: Any) -> Tuple[Any, int]:
    """
    Remove edges whose midpoint lies inside hazard polygons.
    Returns a tuple (G_modified, blocked_count).

    Works with networkx graphs and dict fallback. If networkx is not available or G is not a graph,
    returns (G, 0).
    """
    if G is None or hazard_polygons is None:
        return G, 0
    blocked = 0
    try:
        geoms = list(_iter_hazard_geoms(hazard_polygons))
        if not geoms:
            return G, 0
        # networkx graph path
        if nx is not None and isinstance(G, nx.Graph):
            G2 = G.copy()
            for u, v, data in list(G.edges(data=True)):
                try:
                    # compute midpoint robustly
                    midpoint = None
                    # If edge has geometry attribute (shapely), use centroid
                    if data and "geometry" in data and hasattr(data["geometry"], "centroid"):
                        try:
                            midpoint = data["geometry"].centroid
                        except Exception:
                            midpoint = None
                    if midpoint is None:
                        # nodes may store x,y or lon,lat or coord
                        ux = G.nodes[u].get("x", G.nodes[u].get("lon", None))
                        uy = G.nodes[u].get("y", G.nodes[u].get("lat", None))
                        vx = G.nodes[v].get("x", G.nodes[v].get("lon", None))
                        vy = G.nodes[v].get("y", G.nodes[v].get("lat", None))
                        if None in (ux, uy, vx, vy):
                            # fallback: if nodes are tuple coords (i,j) or (lat,lon)
                            try:
                                if isinstance(u, tuple) and len(u) >= 2 and isinstance(u[0], (int, float)):
                                    u_lat = float(u[0]); u_lon = float(u[1])
                                else:
                                    u_lat = float(G.nodes[u].get("y", 0.0)); u_lon = float(G.nodes[u].get("x", 0.0))
                                if isinstance(v, tuple) and len(v) >= 2 and isinstance(v[0], (int, float)):
                                    v_lat = float(v[0]); v_lon = float(v[1])
                                else:
                                    v_lat = float(G.nodes[v].get("y", 0.0)); v_lon = float(G.nodes[v].get("x", 0.0))
                                mid_lat = (u_lat + v_lat) / 2.0
                                mid_lon = (u_lon + v_lon) / 2.0
                                midpoint = Point(mid_lon, mid_lat) if Point is not None else None
                            except Exception:
                                midpoint = None
                        else:
                            # ux,uy,vx,vy likely lon/lat or x/y; create Point(lon, lat)
                            try:
                                mid_lon = (ux + vx) / 2.0
                                mid_lat = (uy + vy) / 2.0
                                midpoint = Point(mid_lon, mid_lat) if Point is not None else None
                            except Exception:
                                midpoint = None
                    if midpoint is None:
                        continue
                    for poly in geoms:
                        try:
                            if hasattr(poly, "contains"):
                                if poly.contains(midpoint):
                                    try:
                                        G2.remove_edge(u, v)
                                        blocked += 1
                                    except Exception:
                                        pass
                                    break
                            else:
                                # if poly not shapely, skip
                                continue
                        except Exception:
                            continue
                except Exception:
                    continue
            return G2, blocked
        # dict fallback: no edge geometry checks possible
        return G, 0
    except Exception as e:
        logger.warning("block_edges_by_hazards error: %s", e)
        return G, blocked


def _nearest_node_in_graph(G: Any, coord: Tuple[float, float]):
    """
    Return a node id nearest to coord for networkx graphs; for dict fallback return coord.
    """
    try:
        if nx is not None and isinstance(G, nx.Graph):
            best = None
            best_d = float("inf")
            for n in G.nodes:
                try:
                    if isinstance(n, tuple) and len(n) == 2 and isinstance(n[0], (int, float)):
                        node_coord = (float(n[0]), float(n[1]))
                    else:
                        node_coord = (float(G.nodes[n].get("y", 0.0)), float(G.nodes[n].get("x", 0.0)))
                    d = _haversine_km(coord, node_coord)
                    if d < best_d:
                        best_d = d
                        best = n
                except Exception:
                    continue
            return best if best is not None else coord
    except Exception:
        pass
    return coord


def compute_shortest_path(G: Any, origin: Tuple[float, float], target: Tuple[float, float], weight: str = "length") -> List[Tuple[float, float]]:
    """
    Compute shortest path. Returns list of (lat, lon) tuples.
    Falls back to straight-line interpolation on error.
    """
    try:
        if G is None:
            return grid_route_fallback(origin, target)
        # networkx graph path
        if nx is not None and isinstance(G, nx.Graph):
            src = _nearest_node_in_graph(G, origin)
            dst = _nearest_node_in_graph(G, target)
            try:
                path = nx.shortest_path(G, source=src, target=dst, weight=weight)
            except Exception:
                path = nx.shortest_path(G, source=src, target=dst)
            coords: List[Tuple[float, float]] = []
            for node in path:
                if isinstance(node, tuple) and len(node) == 2:
                    coords.append((float(node[0]), float(node[1])))
                else:
                    lat = float(G.nodes[node].get("y", origin[0]))
                    lon = float(G.nodes[node].get("x", origin[1]))
                    coords.append((lat, lon))
            return coords if coords else grid_route_fallback(origin, target)
        # dict fallback
        return grid_route_fallback(origin, target)
    except Exception as e:
        logger.warning("compute_shortest_path error: %s", e)
        return grid_route_fallback(origin, target)


def compute_fastest_path(G: Any, origin: Tuple[float, float], target: Tuple[float, float]) -> List[Tuple[float, float]]:
    """
    For demo, same as shortest path. Could be extended to use travel time weights.
    """
    return compute_shortest_path(G, origin, target)


def compute_safest_path(G: Any, origin: Tuple[float, float], target: Tuple[float, float], hazards: Any) -> List[Tuple[float, float]]:
    """
    Safest path: attempt to avoid hazards by removing edges near hazards and computing shortest path on the pruned graph.
    Returns list of (lat, lon) tuples.
    """
    try:
        if G is None:
            return grid_route_fallback(origin, target)
        G_pruned, blocked = block_edges_by_hazards(G, hazards)
        # If block_edges_by_hazards returned a graph-like object, use it; otherwise fallback to original G
        G_use = G_pruned if G_pruned is not None else G
        return compute_shortest_path(G_use, origin, target)
    except Exception as e:
        logger.warning("compute_safest_path error: %s", e)
        return grid_route_fallback(origin, target)


def grid_route_fallback(origin: Tuple[float, float], target: Tuple[float, float], steps: int = 30) -> List[Tuple[float, float]]:
    """Simple straight-line interpolation fallback route between origin and target."""
    try:
        lat1, lon1 = origin
        lat2, lon2 = target
        return [(lat1 + (lat2 - lat1) * i / steps, lon1 + (lon2 - lon1) * i / steps) for i in range(steps + 1)]
    except Exception:
        # If origin/target malformed, return a tiny default route
        return [origin, target] if origin and target else []
