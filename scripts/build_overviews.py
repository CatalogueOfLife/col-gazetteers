"""Build <prefix>/overview.geojson, a small world map of each gazetteer.

Fetching a whole gazetteer one feature at a time is too heavy for an overview
map (`realm`, `iho` and `teow` cost 11-13 MB gzipped, `teow` in 847 requests),
so this writes one simplified file per gazetteer. It reads only the committed
features/ and labels.tsv, and downloads nothing.

There are two kinds of overview:

  * Area overviews show the high level areas of a gazetteer. Features carry
    {id, name, color}, where color is a palette index that differs between
    neighbouring areas, so that consumers can see the boundaries.
  * Coverage overviews (`gi`, `mrgid`) have too many entries to show one by one.
    They hold one MultiPolygon of the grid cells that entries touch, carrying
    {coverage, count, omitted}. Cells keep the cost fixed: merging the shapes of
    `mrgid` directly took 104 minutes and 37 GB. They also keep entries visible
    that would be too small to see at world zoom.

Output is deterministic (sorted, no timestamps), so that a rebuild only commits
real changes.

Needs a case-sensitive filesystem like the rest of the tree, see common/ids.py.

Run with `python scripts/build_overviews.py [prefix ...]`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.config import REPO_ROOT  # noqa: E402
from common.ids import normalize_id  # noqa: E402
from continent.build import share_borders  # noqa: E402
from realm.partition import as_coverage, partition_problems  # noqa: E402

# Colours are palette indices, kept below what a consumer's palette has to offer.
MAX_COLORS = 8
# Areas this close (degrees) are neighbours. Features are simplified one by one
# and do not share exact boundaries, so touching alone would miss most of them.
NEIGHBOUR_DISTANCE = 0.25
# Parts and holes below this area (square degrees, about 11 x 11 km at the equator)
# can't be seen at world zoom, yet make up most of the file for coastal areas.
MIN_PART_AREA = 0.01
COORD_DECIMALS = 3

# Degrees, about 55 km at the equator and not quite 2 pixels on a world map.
COVERAGE_CELL = 0.5
COVERAGE_SIMPLIFY = 0.05


@dataclass(frozen=True)
class AreaOverview:
    tolerance: float
    include: Callable[[str], bool] = lambda _id: True
    # The areas divide the whole globe between them, so they are simplified
    # together and keep sharing their borders (see simplify_partition).
    partition: bool = False
    # The areas share borders but leave the sea uncovered: simplified together too,
    # without the partition's demand to cover the globe.
    shared_borders: bool = False
    # Fixed colours by id, written as the `fill` property.
    fills: Mapping[str, str] | None = None


def _matching(pattern: str, exclude: Iterable[str] = ()) -> Callable[[str], bool]:
    regex = re.compile(pattern)
    excluded = set(exclude)
    return lambda i: i not in excluded and regex.fullmatch(i) is not None


# Fixed colours for the 19 realms, from CARTO's colour schemes
# (https://carto.com/carto-colors/), named per colour: Bold's and Vivid's warm
# colours and greens on land, their blues, teals and purples at sea, near-white
# poles. Realms sharing more than 10° of border are at least ΔE 20 apart (CIELAB).
REALM_FILLS = {
    "Afrotropic": "#f2b701",  # Bold
    "Antarctic": "#f1f1f1",  # Tropic
    "Australasia": "#e73f74",  # Bold
    "Indomalaya": "#99c945",  # Vivid
    "Nearctic": "#f97b72",  # Bold
    "Neotropic": "#24796c",  # Vivid
    "Oceania": "#edef5d",  # ag_GrnYl
    "Palearctic": "#e68310",  # Bold
    "Agulhan": "#11a579",  # Bold
    "Arctic": "#d1eeea",  # Teal
    "Atlantotropic": "#2f8ac4",  # Vivid
    "Boreoatlantic": "#5d69b1",  # Vivid
    "Boreopacific": "#764e9f",  # Vivid
    "Indopacific": "#008695",  # Bold
    "Magellanic": "#7f3c8d",  # Bold
    "Mesopacific": "#52bca3",  # Vivid
    "Panamic": "#3969ac",  # Bold
    "SouthernOcean": "#a8dbd9",  # Teal
    "Tasmanic": "#4b4b8f",  # Bold
}

# Fixed colours for the 7 continents, from CARTO's Bold and Vivid. Antarctica is
# Vivid's grey, as on a pale sea the near-white of the realm maps would disappear.
CONTINENT_FILLS = {
    "Africa": "#f2b701",
    "Antarctica": "#a5aa99",
    "Asia": "#e73f74",
    "Europe": "#3969ac",
    "NorthAmerica": "#11a579",
    "Oceania": "#e68310",
    "SouthAmerica": "#7f3c8d",
}

AREA_OVERVIEWS: dict[str, AreaOverview] = {
    "continent": AreaOverview(0.1, shared_borders=True, fills=CONTINENT_FILLS),
    # Level 1 continents only, the lower levels subdivide them.
    "tdwg": AreaOverview(0.02, _matching(r"[1-9]")),
    # Countries only. The historic ISO 3166-3 codes overlap current countries:
    # YU was dissolved from RS, ME, HR..., AN from CW, SX, BQ, and TP is TL.
    "iso": AreaOverview(0.03, _matching(r"[A-Z]{2}", exclude=("YU", "AN", "TP"))),
    # Major areas only, the subdivisions are nested inside them.
    "fao": AreaOverview(0.05, _matching(r"[0-9]{2}")),
    # A lowercase suffix marks a sub-area (1a Gulf of Bothnia in 1 Baltic Sea).
    # An uppercase one is a peer (28A and 28B, the two Mediterranean basins).
    # Area 48, the East Indian Archipelago, only exists as its sub-areas 48a-48o.
    "iho": AreaOverview(0.1, _matching(r"[0-9]+[A-Z]?|48[a-z]")),
    "realm": AreaOverview(0.1, partition=True, fills=REALM_FILLS),
    "longhurst": AreaOverview(0.1),
    "costello": AreaOverview(0.1),
    "teow": AreaOverview(0.1),
}


@dataclass(frozen=True)
class CoverageOverview:
    # Square degrees. Larger entries are left out, as they would hide the rest.
    max_area: float | None = None
    # Values of the third column of labels.tsv that are left out.
    exclude_types: frozenset[str] = frozenset()


COVERAGE_OVERVIEWS: dict[str, CoverageOverview] = {
    "gi": CoverageOverview(),
    # Everything in Marine Regions together is just the world: 63392 is a box
    # around it, others are oceans, realms and whole continents. The excluded
    # types are survey grids and boundaries, not places. They would only draw
    # their lattice, the ICES rectangles alone are a sixth of all entries.
    "mrgid": CoverageOverview(
        max_area=10,
        exclude_types=frozenset(
            {"ICES Statistical Rectangles", "Mineral Rights Areas", "HAB monitoring grid", "OSPAR Boundary"}
        ),
    ),
}


def read_labels(prefix_dir: Path) -> list[tuple[str, str, str]]:
    """The (id, name, type) rows of a labels.tsv, sorted by id."""
    rows = []
    with (prefix_dir / "labels.tsv").open(encoding="utf-8", newline="\n") as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if cols[0]:
                name, place_type = (cols[1:3] + ["", ""])[:2]
                rows.append((cols[0], name, place_type))
    return sorted(rows)


def read_geometry(prefix_dir: Path, area_id: str) -> BaseGeometry | None:
    path = prefix_dir / "features" / f"{normalize_id(area_id)}.geojson"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        geometry = json.load(f).get("geometry")
    if not geometry:
        return None
    geom = shape(geometry)
    return None if geom.is_empty else geom


def _polygons(geom: BaseGeometry) -> list[Polygon]:
    """The polygons of any geometry, e.g. the collection make_valid may return."""
    parts = shapely.get_parts(geom)
    if geom.geom_type == "GeometryCollection":
        return [p for part in parts for p in _polygons(part)]
    return [p for p in parts if isinstance(p, Polygon)]


def _without_tiny_holes(polygon: Polygon) -> Polygon:
    holes = [r for r in polygon.interiors if Polygon(r).area >= MIN_PART_AREA]
    return Polygon(polygon.exterior, holes)


def _without_tiny_parts(parts: Sequence[Polygon]) -> Polygon | MultiPolygon:
    """The parts and holes of at least MIN_PART_AREA, and always the largest part."""
    largest = max(parts, key=lambda p: p.area)
    kept = [_without_tiny_holes(p) for p in parts if p is largest or p.area >= MIN_PART_AREA]
    return MultiPolygon(kept) if len(kept) > 1 else kept[0]


def simplify_area(geom: BaseGeometry, tolerance: float) -> BaseGeometry:
    """Simplify a polygonal area, always keeping at least its largest part."""
    simplified = shapely.make_valid(geom.simplify(tolerance, preserve_topology=True))
    parts = _polygons(simplified)
    if not parts:
        return geom
    result = _without_tiny_parts(parts)
    # Snapping to the grid keeps the shape valid, which rounding alone does not.
    # It can collapse the very smallest areas, which then stay as they were.
    snapped = shapely.set_precision(result, 10**-COORD_DECIMALS)
    return result if snapped.is_empty else snapped


def simplify_partition(areas: Mapping[str, BaseGeometry], tolerance: float) -> dict[str, BaseGeometry]:
    """Simplify areas that divide the globe between them, keeping it divided.

    Simplified one by one, neighbours would overlap and leave gaps along every
    shared border; coverage_simplify moves each border once, for both sides.
    It keeps every ring, so tiny parts and holes are dropped as in
    simplify_area. Snapping all borders onto the grid together then gives what
    they leave to the neighbour they border most.
    """
    keys = list(areas)
    simplified = shapely.coverage_simplify([areas[k] for k in keys], tolerance)
    trimmed = {k: _without_tiny_parts(_polygons(g)) for k, g in zip(keys, simplified)}
    snapped, _ = as_coverage(trimmed, 10**-COORD_DECIMALS)
    problems = partition_problems(snapped)
    if problems:
        raise ValueError("simplified areas do not partition the globe:\n  " + "\n  ".join(problems))
    return {k: g.geoms[0] if len(g.geoms) == 1 else g for k, g in snapped.items()}


def simplify_shared_borders(areas: Mapping[str, BaseGeometry], tolerance: float) -> dict[str, BaseGeometry]:
    """Simplify areas that share borders but don't cover the globe, keeping the
    borders shared. Tiny parts and holes are dropped as in simplify_area."""
    keys = list(areas)
    simplified = shapely.coverage_simplify([areas[k] for k in keys], tolerance)
    trimmed = {k: _without_tiny_parts(_polygons(g)) for k, g in zip(keys, simplified)}
    snapped, _ = share_borders(trimmed, 10**-COORD_DECIMALS)
    return {k: g.geoms[0] if len(g.geoms) == 1 else g for k, g in snapped.items()}


def _wrapped(geom: BaseGeometry) -> list[BaseGeometry]:
    """The geometry plus its copies across the antimeridian, if it gets close."""
    minx, _, maxx, _ = geom.bounds
    copies = [geom]
    if minx - NEIGHBOUR_DISTANCE <= -180:
        copies.append(shapely.transform(geom, lambda c: c + [360, 0]))
    if maxx + NEIGHBOUR_DISTANCE >= 180:
        copies.append(shapely.transform(geom, lambda c: c - [360, 0]))
    return copies


def find_neighbours(geoms: Sequence[BaseGeometry]) -> list[set[int]]:
    tree = shapely.STRtree(geoms)
    neighbours: list[set[int]] = [set() for _ in geoms]
    for i, geom in enumerate(geoms):
        for copy in _wrapped(geom):
            for j in tree.query(copy, predicate="dwithin", distance=NEIGHBOUR_DISTANCE):
                if int(j) != i:
                    neighbours[i].add(int(j))
                    neighbours[int(j)].add(i)
    return neighbours


def assign_colors(neighbours: Sequence[set[int]]) -> list[int]:
    """DSATUR graph colouring: colour the most constrained node first.

    Prefers the least used colour that is still free, as the lowest free one
    would leave most of the palette unused on sparse graphs like `realm`.
    Ties are broken by index, which keeps the result deterministic.
    """
    colors: list[int | None] = [None] * len(neighbours)
    used = [0] * MAX_COLORS
    for _ in neighbours:
        node = min(
            (i for i, c in enumerate(colors) if c is None),
            key=lambda i: (-len({colors[n] for n in neighbours[i]} - {None}), -len(neighbours[i]), i),
        )
        taken = {colors[n] for n in neighbours[node]}
        free = [c for c in range(MAX_COLORS) if c not in taken]
        if not free:
            raise ValueError(f"needs more than {MAX_COLORS} colours at node {node}")
        color = min(free, key=lambda c: (used[c], c))
        colors[node] = color
        used[color] += 1
    return colors  # type: ignore[return-value]


def _rounded(coords: object) -> object:
    if isinstance(coords[0], (int, float)):  # type: ignore[index]
        return [round(c, COORD_DECIMALS) for c in coords]  # type: ignore[union-attr]
    return [_rounded(c) for c in coords]  # type: ignore[union-attr]


def _feature(geom: BaseGeometry, properties: dict) -> dict:
    geometry = mapping(geom)
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": geometry["type"], "coordinates": _rounded(geometry["coordinates"])},
    }


def build_area_overview(rows: Sequence[tuple[str, str, BaseGeometry]], config: AreaOverview) -> dict:
    """An overview of (id, name, geometry) rows, with neighbours coloured apart."""
    if config.partition:
        simplified = simplify_partition({area_id: geom for area_id, _, geom in rows}, config.tolerance)
        geoms = [simplified[area_id] for area_id, _, _ in rows]
    elif config.shared_borders:
        simplified = simplify_shared_borders({area_id: geom for area_id, _, geom in rows}, config.tolerance)
        geoms = [simplified[area_id] for area_id, _, _ in rows]
    else:
        geoms = [simplify_area(geom, config.tolerance) for _, _, geom in rows]
    colors = assign_colors(find_neighbours(geoms))
    if config.fills is not None:
        missing = [area_id for area_id, _, _ in rows if area_id not in config.fills]
        if missing:
            raise ValueError(f"no fill for {', '.join(missing)}")
    features = []
    for (area_id, name, _), geom, color in zip(rows, geoms, colors):
        properties = {"id": area_id, "name": name, "color": color}
        if config.fills is not None:
            properties["fill"] = config.fills[area_id]
        features.append(_feature(geom, properties))
    return {"type": "FeatureCollection", "features": features}


def _world_grid() -> np.ndarray:
    xs = np.arange(-180, 180, COVERAGE_CELL)
    ys = np.arange(-90, 90, COVERAGE_CELL)
    x, y = (a.ravel() for a in np.meshgrid(xs, ys))
    return shapely.box(x, y, x + COVERAGE_CELL, y + COVERAGE_CELL)


def build_coverage_overview(
    rows: Iterable[tuple[str, BaseGeometry]], config: CoverageOverview = CoverageOverview()
) -> dict:
    """The grid cells touched by any mix of points, lines and polygons.

    rows are (type, geometry) pairs.
    """
    cells = _world_grid()
    tree = shapely.STRtree(cells)
    occupied = np.zeros(len(cells), dtype=bool)
    count = omitted = 0
    for place_type, geom in rows:
        if place_type in config.exclude_types or (config.max_area is not None and geom.area > config.max_area):
            omitted += 1
            continue
        count += 1
        occupied[tree.query(geom.simplify(COVERAGE_SIMPLIFY, preserve_topology=True), predicate="intersects")] = True
    if not occupied.any():
        raise ValueError("no geometries to cover")
    coverage = MultiPolygon(_polygons(shapely.coverage_union_all(cells[occupied])))
    return {
        "type": "FeatureCollection",
        "features": [_feature(coverage, {"coverage": True, "count": count, "omitted": omitted})],
    }


def write_overview(prefix_dir: Path, overview: dict) -> Path:
    path = prefix_dir / "overview.geojson"
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(overview, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    return path


def _report(prefix: str, overview: dict, path: Path, started: float) -> None:
    features = overview["features"]
    colors = {f["properties"]["color"] for f in features if "color" in f["properties"]}
    parts = f"{len(colors)} colours" if colors else f"covers {features[0]['properties']['count']} entries"
    print(
        f"[{prefix}] {len(features)} features, {parts}, "
        f"{path.stat().st_size / 1e6:.2f} MB, {time.monotonic() - started:.0f}s",
        flush=True,
    )


def build_prefix(prefix: str, root: Path = REPO_ROOT) -> None:
    started = time.monotonic()
    prefix_dir = root / prefix
    labels = read_labels(prefix_dir)
    if prefix in COVERAGE_OVERVIEWS:
        rows = ((place_type, read_geometry(prefix_dir, area_id)) for area_id, _, place_type in labels)
        overview = build_coverage_overview(((t, g) for t, g in rows if g is not None), COVERAGE_OVERVIEWS[prefix])
    else:
        config = AREA_OVERVIEWS[prefix]
        rows = []
        for area_id, name, _ in labels:
            if config.include(area_id):
                geom = read_geometry(prefix_dir, area_id)
                if geom is not None:
                    rows.append((area_id, name, geom))
        overview = build_area_overview(rows, config)
    _report(prefix, overview, write_overview(prefix_dir, overview), started)


def main() -> int:
    known = [*AREA_OVERVIEWS, *COVERAGE_OVERVIEWS]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prefixes", nargs="*", help=f"default: all of {', '.join(known)}")
    args = parser.parse_args()
    unknown = set(args.prefixes) - set(known)
    if unknown:
        parser.error(f"no overview for {', '.join(sorted(unknown))}")
    for prefix in args.prefixes or known:
        build_prefix(prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
