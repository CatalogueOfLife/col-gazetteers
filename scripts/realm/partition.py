"""Turn the realm source polygons into a partition of the whole globe.

The terrestrial (RESOLVE) and marine (Costello) inputs are drawn on
different coastlines. RESOLVE also leaves holes: lakes, gaps between
simplified ecoregions, and "Rock and Ice" (the Greenland and Antarctic ice
sheets, glaciers), which has no realm. The `realm` gazetteer instead divides
the entire globe into its realms, with no gaps and no overlaps. Land takes
precedence over sea. Neighbouring realms share their borders vertex for
vertex (`as_coverage`), so a border has no width.

All geometry here is EPSG:4326 lon/lat, treated as planar. That is fine for
deciding adjacency and containment, which is all this module does.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping

import numpy as np
import shapely
from shapely import STRtree
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry

WORLD = box(-180, -90, 180, 90)
# Costello's realms stop at 89°N; the cap above is open sea.
NORTH_POLAR_SEA = box(-180, 89, 180, 90)
# RESOLVE's rock & ice stops at 89.89°S; the cap below is the Antarctic ice sheet.
SOUTH_POLAR_LAND = box(-180, -90, 180, -89.5)
# Leftovers south of this — neither RESOLVE land nor Costello sea — are
# Antarctic ice shelves that Costello leaves out (it includes Ross and Ronne).
ICE_SHELF_LATITUDE = -60.0


def polygonal(geom) -> MultiPolygon:
    """The polygonal parts of any geometry, as one MultiPolygon.

    Overlay results can come back as GeometryCollections carrying stray
    lines and points along shared edges; those are dropped.
    """
    polys: list[Polygon] = []
    stack = [geom]
    while stack:
        g = stack.pop()
        if g is None or g.is_empty:
            continue
        if g.geom_type == "Polygon":
            polys.append(g)
        elif g.geom_type in ("MultiPolygon", "GeometryCollection"):
            stack.extend(shapely.get_parts(g))
    return MultiPolygon(polys)


def union(geoms: Iterable) -> MultiPolygon:
    return polygonal(shapely.union_all([g for g in geoms if not g.is_empty]))


def fill_holes(mp: MultiPolygon) -> tuple[MultiPolygon, int]:
    """Drop every interior ring. Returns (filled, number of holes filled)."""
    holes = sum(len(p.interiors) for p in mp.geoms)
    return union(Polygon(p.exterior) for p in mp.geoms), holes


def snap_antimeridian(geom, tolerance: float = 1e-4):
    """Move vertices within `tolerance` of ±180° onto it.

    Costello's polygons stop at ±179.99999°. That leaves a hairline gap
    along the antimeridian, which would join unrelated leftovers (the Ross
    Ice Shelf, the polar caps) into one piece.
    """
    def snap(coords):
        x = coords[:, 0]
        near = np.abs(x) > 180 - tolerance
        coords[near, 0] = np.sign(x[near]) * 180
        return coords
    # Snapping can collapse a sliver ring onto the antimeridian; repair it.
    return polygonal(shapely.make_valid(shapely.transform(geom, snap)))


def make_disjoint(layers: dict[str, MultiPolygon]) -> dict[str, MultiPolygon]:
    """Remove overlaps between layers; earlier keys win."""
    out: dict[str, MultiPolygon] = {}
    taken = MultiPolygon()
    for key, geom in layers.items():
        out[key] = polygonal(geom.difference(taken))
        taken = union([taken, out[key]])
    return out


def clip(geom, minx: float, miny: float, maxx: float, maxy: float):
    """`geom` cut to a rectangle. GEOS's fast rectangle clip occasionally
    emits a ring of fewer than 4 points from overlay output; fall back to a
    full intersection when it does."""
    try:
        return shapely.clip_by_rect(geom, minx, miny, maxx, maxy)
    except GEOSException:
        return shapely.intersection(geom, box(minx, miny, maxx, maxy))


def tiles(geom: MultiPolygon, size: float = 5.0) -> list[Polygon]:
    """Cut `geom` along a `size`° grid, so spatial queries touch small pieces
    instead of a continent-sized polygon. Cutting also lets a big leftover
    (an ice sheet on a realm border) be shared out between its neighbours."""
    if geom.is_empty:
        return []
    minx, miny, maxx, maxy = geom.bounds
    out: list[Polygon] = []
    x = -180 + size * ((minx + 180) // size)
    while x < maxx:
        y = -90 + size * ((miny + 90) // size)
        while y < maxy:
            out.extend(polygonal(clip(geom, x, y, x + size, y + size)).geoms)
            y += size
        x += size
    return out


def _shared_border(boundary, neighbour: Polygon) -> float:
    """Length of `boundary` (a piece's outline) that runs along `neighbour`."""
    minx, miny, maxx, maxy = neighbour.bounds
    local = clip(boundary, minx - 1e-6, miny - 1e-6, maxx + 1e-6, maxy + 1e-6)
    if local.is_empty:
        return 0.0
    return shapely.intersection(local, neighbour).length


def assign_pieces(
    pieces: Iterable[Polygon],
    realms: dict[str, MultiPolygon],
) -> tuple[dict[str, list[Polygon]], int]:
    """Give each piece to the realm it shares the longest border with.

    Pieces that only touch other unassigned pieces (the interior of an ice
    sheet) are resolved over further passes, as their neighbours get
    assigned. Whatever still touches nothing (an isolated ice-covered
    island) goes to the nearest realm. Returns ({realm: [pieces]}, number
    resolved by distance rather than adjacency).
    """
    keys = list(realms)
    items: list[Polygon] = []
    owner: list[int] = []
    for k, key in enumerate(keys):
        cut = tiles(realms[key])
        items.extend(cut)
        owner.extend([k] * len(cut))
    return _assign_to_neighbours(pieces, keys, items, owner)


def _assign_to_neighbours(
    pieces: Iterable[Polygon],
    keys: list[str],
    items: list[Polygon],
    owner: list[int],
) -> tuple[dict[str, list[Polygon]], int]:
    """`assign_pieces` against polygons already placed: `items[i]` is in realm
    `keys[owner[i]]`."""
    items, owner = list(items), list(owner)  # pieces join them as they get assigned
    assigned: dict[str, list[Polygon]] = defaultdict(list)
    pending = [p for p in pieces if not p.is_empty]

    while pending:
        tree = STRtree(items)
        progressed, still = [], []
        for piece in pending:
            boundary = piece.boundary
            votes: dict[int, float] = defaultdict(float)
            for i in tree.query(piece, predicate="intersects"):
                votes[owner[i]] += _shared_border(boundary, items[i])
            best = max(votes, key=lambda k: votes[k], default=None)
            if best is None or votes[best] <= 0:
                still.append(piece)
            else:
                progressed.append((piece, best))
        for piece, k in progressed:
            assigned[keys[k]].append(piece)
            items.append(piece)
            owner.append(k)
        if not progressed:
            break
        pending = still

    tree = STRtree(items)
    for piece in pending:
        assigned[keys[owner[int(tree.nearest(piece))]]].append(piece)
    return assigned, len(pending)


def partition_globe(
    land: dict[str, list],
    unassigned_land: list,
    sea: dict[str, list],
    *,
    north_polar_realm: str,
    ice_shelf_realm: str,
    log: Callable[[str], None] = print,
) -> dict[str, MultiPolygon]:
    """Divide the globe between the `land` and `sea` realms.

    `land` and `sea` map realm id → source polygons. `unassigned_land` is land
    with no realm (RESOLVE's rock & ice). In order:

    1. Land is everything RESOLVE covers, with every hole filled: lakes,
       river reaches enclosed by land, and slivers between ecoregions. A land
       realm takes the holes it encloses on its own. Any other realm's enclave
       inside such a hole is kept out.
    2. The remaining land (ice sheets, glaciers, lakes on a realm border) is
       cut into 5° tiles. Each tile goes to the land realm it borders most.
    3. Sea is everything else. A sea realm is its Costello realms clipped to
       the sea, plus the cap north of 89°N for `north_polar_realm`.
    4. Leftovers that are neither (coastline mismatches, river mouths) are
       cut into tiles. Each tile goes to whichever realm, land or sea, it
       borders most. South of 60°S such tiles are ice shelves and all go to
       `ice_shelf_realm`, so that every shelf ends up in the same realm.
    """
    cores = {k: union(snap_antimeridian(g) for g in gs) for k, gs in land.items()}
    ice = [snap_antimeridian(g) for g in unassigned_land] + [SOUTH_POLAR_LAND]
    all_cores = union(cores.values())
    land_mask, n_holes = fill_holes(union([all_cores, *ice]))
    log(f"land: {n_holes} holes filled (lakes, enclosed rivers, slivers between ecoregions)")

    terrestrial = {}
    for k, core in cores.items():
        filled, _ = fill_holes(core)
        terrestrial[k] = polygonal(filled.difference(all_cores.difference(core)))
    terrestrial = make_disjoint(terrestrial)
    rest = polygonal(land_mask.difference(union(terrestrial.values())))
    assigned, by_distance = assign_pieces(tiles(rest), terrestrial)
    log(f"land: {sum(map(len, assigned.values()))} rock & ice / lake tiles given to adjacent land realms"
        f" ({by_distance} by distance)")
    terrestrial = {k: union([g, *assigned.get(k, [])]) for k, g in terrestrial.items()}

    sea_mask = polygonal(WORLD.difference(land_mask))
    marine = {k: polygonal(union(snap_antimeridian(g) for g in gs).intersection(sea_mask))
              for k, gs in sea.items()}
    marine[north_polar_realm] = union([marine[north_polar_realm], NORTH_POLAR_SEA.difference(land_mask)])
    marine = make_disjoint(marine)

    realms = {**terrestrial, **marine}
    rest = tiles(polygonal(sea_mask.difference(union(marine.values()))))
    shelves = [t for t in rest if t.representative_point().y < ICE_SHELF_LATITUDE]
    others = [t for t in rest if t.representative_point().y >= ICE_SHELF_LATITUDE]
    realms[ice_shelf_realm] = union([realms[ice_shelf_realm], *shelves])
    assigned, by_distance = assign_pieces(others, realms)
    log(f"coast: {len(shelves)} ice shelf tiles to {ice_shelf_realm}, {len(others)} coastline"
        f" mismatch tiles to adjacent realms ({by_distance} by distance)")
    return {k: union([g, *assigned.get(k, [])]) for k, g in realms.items()}


def as_coverage(
    realms: Mapping[str, BaseGeometry], grid_size: float
) -> tuple[dict[str, MultiPolygon], int]:
    """`realms` redrawn on a `grid_size` grid as an exact partition of WORLD.

    Rounding each realm on its own leaves hairline gaps and overlaps between
    neighbours, and the two sides of a border need not run through the same
    vertices. Instead, all realm borders are snap-rounded together into one
    network of edges, which cuts the world into faces. A face goes to the
    realm its interior lies in. The few slivers that lie in no realm, or in
    two, go to the realm they border most. Each realm is then the union of
    its faces, so neighbours are built from the very same edges: borders
    have no width and match vertex for vertex. Returns (realms, number of
    slivers).
    """
    keys = list(realms)
    geoms = [realms[k] for k in keys]
    edges = shapely.union_all([*shapely.boundary(geoms), WORLD.boundary], grid_size=grid_size)
    faces = list(shapely.get_parts(shapely.polygonize(shapely.get_parts(edges))))
    face, realm = STRtree(geoms).query(shapely.point_on_surface(faces), predicate="within")
    count = np.bincount(face, minlength=len(faces))
    owner = {int(f): int(r) for f, r in zip(face, realm) if count[f] == 1}
    slivers = [g for i, g in enumerate(faces) if i not in owner]
    placed = sorted(owner)
    assigned, _ = _assign_to_neighbours(slivers, keys, [faces[i] for i in placed], [owner[i] for i in placed])
    parts: dict[str, list[Polygon]] = defaultdict(list)
    for i in placed:
        parts[keys[owner[i]]].append(faces[i])
    for key, pieces in assigned.items():
        parts[key].extend(pieces)
    return {key: polygonal(shapely.coverage_union_all(parts[key])) for key in keys}, len(slivers)


def partition_problems(realms: Mapping[str, BaseGeometry]) -> list[str]:
    """Why `realms` are not an exact partition of WORLD; empty when they are.

    Exact means valid polygons that cover WORLD with no gap and no overlap,
    and neighbours that share each border vertex for vertex.
    """
    problems = [f"{k}: not a valid polygon ({shapely.is_valid_reason(g)})"
                for k, g in realms.items() if not g.is_valid]
    if problems:
        return problems  # the checks below need valid polygons
    geoms = list(realms.values())
    for k, edges in zip(realms, shapely.coverage_invalid_edges(geoms)):
        if edges is not None and not edges.is_empty:
            problems.append(f"{k}: {edges.length:.6f}° of border overlaps a neighbour or misses its vertices")
    covered = shapely.union_all(geoms) if problems else shapely.coverage_union_all(geoms)
    gap, outside = WORLD.difference(covered), covered.difference(WORLD)
    if not gap.is_empty:
        problems.append(f"{gap.area:.6f} sq° of the world is in no realm")
    if not outside.is_empty:
        problems.append(f"{outside.area:.6f} sq° of the realms lies outside the world")
    return problems
