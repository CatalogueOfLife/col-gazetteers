"""Fitting subdivisions into GBIF's countries.

A subdivision may lie in the land of its ISO parent and of the territories ISO
also codes under that parent (FR-GF lies in GF). Candidates come in priority
order (OSM, OSM via Wikidata, Natural Earth, geoBoundaries, derived, then the
placeholder), and are tried in three passes:

1. every real candidate against the land: the first that keeps MIN_KEEP of its area wins;
2. every real candidate against land + EEZ, for islands ESRI's coastline misses;
   the caller then adds what was kept to the land (`absorb`);
3. the placeholder, against land, then land + EEZ, kept however small.

If nothing fits, the first real candidate is kept unclipped as a conflict,
naming the code whose area holds most of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from shapely.geometry.base import BaseGeometry

from .geom import clip_to_box, polys
from .model import Candidate, Countries, FitResult

MIN_KEEP = 0.10
PLACEHOLDERS = ("placeholder-circle", "placeholder-country-centroid")


def territories(parent: str, dual: Mapping[str, str]) -> set[str]:
    """Country codes ISO also codes as subdivisions of `parent`."""
    return {target for code, target in dual.items() if code.split("-")[0] == parent}


def _clip(geom: BaseGeometry, area: BaseGeometry) -> BaseGeometry:
    if area.is_empty:
        return polys(None)
    # Clip the (often huge) country to the candidate's box first, which is cheap.
    local = clip_to_box(area, *geom.bounds)
    return polys(geom.intersection(local)) if not local.is_empty else polys(None)


def fit(candidates: Sequence[Candidate], land: BaseGeometry, eez: BaseGeometry,
        holders: Mapping[str, BaseGeometry]) -> FitResult:
    real = [c for c in candidates if c.resolution not in PLACEHOLDERS and not c.geom.is_empty]
    for area, mode in ((land, "land"), (eez, "eez")):
        for candidate in real:
            kept = _clip(candidate.geom, area)
            if kept.area >= MIN_KEEP * candidate.geom.area:
                return FitResult(kept, candidate, mode)
    for candidate in (c for c in candidates if c.resolution in PLACEHOLDERS):
        for area, mode in ((land, "land"), (eez, "eez")):
            kept = _clip(candidate.geom, area)
            if not kept.is_empty:
                return FitResult(kept, candidate, mode)
    best = real[0] if real else candidates[0]
    holder = max(holders, key=lambda code: _clip(best.geom, holders[code]).area, default="")
    if holder and _clip(best.geom, holders[holder]).is_empty:
        holder = ""
    return FitResult(best.geom, best, "conflict", holder)


def absorb(countries: Countries, codes: Iterable[str], geom: BaseGeometry) -> None:
    """Add to each code's land the part of `geom` that lies in its land + EEZ."""
    for code in codes:
        if code not in countries.eez:
            continue
        piece = _clip(geom, countries.eez[code])
        if not piece.is_empty:
            countries.land[code] = polys(countries.land[code].union(piece))
