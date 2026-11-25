"""The last resort: a 0.5° circle where Wikidata places the id, or in its country."""

from __future__ import annotations

from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from .model import Candidate
from .universe import WdCode

RADIUS = 0.5


def circle(lon: float, lat: float) -> Polygon:
    """A 32-sided circle of 0.5° radius."""
    return Point(lon, lat).buffer(RADIUS, quad_segs=8)


def placeholder(code: str, wd: WdCode | None, country_land: BaseGeometry) -> Candidate:
    if wd is not None and wd.lon is not None and wd.lat is not None:
        return Candidate(circle(wd.lon, wd.lat), "placeholder-circle", "placeholder-circle",
                         note="coords from Wikidata P625")
    point = country_land.representative_point()
    return Candidate(circle(point.x, point.y), "placeholder-country-centroid", "placeholder-country-centroid",
                     note=f"no coords in Wikidata; inside {code.split('-')[0]}")
