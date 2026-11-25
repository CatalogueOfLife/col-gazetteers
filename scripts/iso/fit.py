"""Fitting subdivisions into GBIF's countries.

A subdivision may lie in the land of its ISO parent and of the territories ISO
also codes under that parent (FR-GF lies in GF). Candidates come in priority
order (OSM, OSM via Wikidata, Natural Earth, geoBoundaries, derived, then the
placeholder), and are tried in three passes:

1. every real candidate against the land: the first that keeps MIN_KEEP of its area wins;
2. every real candidate against land + EEZ, for islands ESRI's coastline misses, with
   land-like sources (Natural Earth, geoBoundaries, derived) first. Only those are added
   to the country's land (`absorb`): OSM's boundaries of island states often enclose
   their sea, and would turn it into land;
3. the placeholder, against land, then land + EEZ, kept however small and never absorbed.

If nothing fits, the first real candidate is kept unclipped as a conflict,
naming the code whose area holds most of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from shapely.geometry.base import BaseGeometry

from .countries import HISTORIC
from .geom import clip_to_box, polys
from .model import Candidate, Countries, FitResult

MIN_KEEP = 0.10
PLACEHOLDERS = ("placeholder-circle", "placeholder-country-centroid")
LAND_LIKE = ("natural-earth", "geoboundaries", "derived")
# Territories that hold a parent's subdivisions without a dual code of their own:
# NL-BQ1..3 (Bonaire, Saba, Sint Eustatius) lie in BQ.
EXTRA_TERRITORIES = {"NL": {"BQ"}}


def territories(parent: str, dual: Mapping[str, str]) -> set[str]:
    """Country codes ISO also codes as subdivisions of `parent`, or whose land holds some of them."""
    found = {target for code, target in dual.items() if code.split("-")[0] == parent}
    return found | EXTRA_TERRITORIES.get(parent, set())


def _clip(geom: BaseGeometry, area: BaseGeometry) -> BaseGeometry:
    if area.is_empty:
        return polys(None)
    # Clip the (often huge) country to the candidate's box first, which is cheap.
    local = clip_to_box(area, *geom.bounds)
    return polys(geom.intersection(local)) if not local.is_empty else polys(None)


def fit(candidates: Sequence[Candidate], land: BaseGeometry, eez: BaseGeometry,
        holders: Mapping[str, BaseGeometry]) -> FitResult:
    real = [c for c in candidates if c.resolution not in PLACEHOLDERS and not c.geom.is_empty]
    for candidate in real:
        kept = _clip(candidate.geom, land)
        if kept.area >= MIN_KEEP * candidate.geom.area:
            return FitResult(kept, candidate, "land")
    # sorted() is stable: land-like sources first, each group in priority order.
    for candidate in sorted(real, key=lambda c: c.resolution not in LAND_LIKE):
        kept = _clip(candidate.geom, eez)
        if kept.area >= MIN_KEEP * candidate.geom.area:
            return FitResult(kept, candidate, "eez", absorb=candidate.resolution in LAND_LIKE)
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
            # YU and AN are merged from their successors, so they take the island too.
            for historic, successors in HISTORIC.items():
                if code in successors and historic in countries.land:
                    countries.land[historic] = polys(countries.land[historic].union(piece))


def untried(fitted: Mapping[str, FitResult], tried: Iterable[str]) -> list[str]:
    """Ids that ended as a placeholder although geoBoundaries wasn't asked for them, because
    an OSM or Natural Earth outline was there but didn't fit."""
    tried = set(tried)
    return sorted(code for code, r in fitted.items()
                  if r.candidate.resolution in PLACEHOLDERS and code not in tried)
