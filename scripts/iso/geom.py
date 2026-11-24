"""Small geometry helpers for the iso build."""

from __future__ import annotations

import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry


def clip_to_box(geom: BaseGeometry, xmin: float, ymin: float, xmax: float, ymax: float) -> BaseGeometry:
    """`geom` within a box. GEOS's fast rectangle clip raises on some valid shapes (parts of
    Azerbaijan and Denmark in GBIF's countries), so those take a regular overlay instead."""
    try:
        return polys(shapely.clip_by_rect(geom, xmin, ymin, xmax, ymax))
    except shapely.errors.GEOSException:
        return polys(geom.intersection(shapely.box(xmin, ymin, xmax, ymax)))


def polys(geom: BaseGeometry | None) -> BaseGeometry:
    """Only the polygonal parts of a (repaired) geometry, or an empty Polygon."""
    if geom is None or geom.is_empty:
        return Polygon()
    geom = shapely.make_valid(geom)
    parts = [p for p in shapely.get_parts(geom) if p.geom_type in ("Polygon", "MultiPolygon")]
    if not parts:
        return Polygon()
    return parts[0] if len(parts) == 1 else shapely.union_all(parts)
