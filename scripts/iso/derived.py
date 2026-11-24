"""Withdrawn codes that are the union of shapes the gazetteer has."""

from __future__ import annotations

from collections.abc import Mapping

import shapely
from shapely.geometry.base import BaseGeometry

from .geom import polys
from .model import Candidate


def _fr(*departements: str) -> tuple[str, ...]:
    return tuple(f"FR-{d}" for d in departements)


DERIVED: dict[str, tuple[str, ...]] = {
    "GB-GBN": ("GB-ENG", "GB-SCT", "GB-WLS"),
    "GB-EAW": ("GB-ENG", "GB-WLS"),
    "CS-CG": ("ME",), "YU-CG": ("ME",),
    "CS-SR": ("RS",), "YU-SR": ("RS",),
    # French régions OSM doesn't tag: Corsica, and pre-2016 régions that 2016 left unchanged.
    "FR-COR": _fr("2A", "2B"),
    "FR-E": _fr("22", "29", "35", "56"),
    "FR-F": _fr("18", "28", "36", "37", "41", "45"),
    "FR-H": _fr("2A", "2B"),
    "FR-J": _fr("75", "77", "78", "91", "92", "93", "94", "95"),
    "FR-R": _fr("44", "49", "53", "72", "85"),
    "FR-U": _fr("04", "05", "06", "13", "83", "84"),
}


def derive(fitted: Mapping[str, BaseGeometry]) -> dict[str, Candidate]:
    """A candidate for every DERIVED id whose parts all have a shape."""
    out = {}
    for code, parts in DERIVED.items():
        if all(p in fitted and not fitted[p].is_empty for p in parts):
            out[code] = Candidate(polys(shapely.union_all([fitted[p] for p in parts])), "derived", "derived",
                                  note=f"union of {', '.join(parts)}")
    return out
