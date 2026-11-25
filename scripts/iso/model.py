"""Types shared by the iso build's modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry.base import BaseGeometry

# A row of GBIF's political layer: the union's attributes with lower-case keys,
# plus "geom" and, once assigned, "codes" (alpha-2 codes it counts for).
Row = dict[str, Any]


@dataclass
class Candidate:
    """One source's shape for an id, before fitting."""

    geom: BaseGeometry
    source: str  # properties.source of the written feature, e.g. "osm-relation-51477"
    resolution: str  # osm, osm-wikidata, natural-earth, geoboundaries, derived, placeholder-circle, ...
    note: str = ""
    label: str = ""  # the source's own name, for new ids without a Wikidata label


@dataclass
class FitResult:
    geom: BaseGeometry
    candidate: Candidate
    mode: str  # "land", "eez" or "conflict"
    holder: str = ""  # for conflicts: the code whose area covers most of the shape
    absorb: bool = False  # an island to add to the country's land (only land-like sources in the EEZ pass)


@dataclass
class Countries:
    land: dict[str, BaseGeometry] = field(default_factory=dict)
    eez: dict[str, BaseGeometry] = field(default_factory=dict)
    rows: dict[str, list[str]] = field(default_factory=dict)  # code -> "<union> (<pol_type>)" of its rows
