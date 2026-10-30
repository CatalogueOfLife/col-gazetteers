"""Turn the realm source polygons into a partition of the whole globe.

The terrestrial (RESOLVE) and marine (Costello) inputs are drawn on
different coastlines. RESOLVE also leaves holes: lakes, gaps between
simplified ecoregions, and "Rock and Ice" (the Greenland and Antarctic ice
sheets, glaciers), which has no realm. The `realm` gazetteer instead divides
the entire globe into its realms, with no gaps and no overlaps. Land takes
precedence over sea.

All geometry here is EPSG:4326 lon/lat, treated as planar. That is fine for
deciding adjacency and containment, which is all this module does.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

import numpy as np
import shapely
from shapely import STRtree
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon, box

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
    items: list[Polygon] = []  # realm tiles, then pieces as they get assigned
    owner: list[int] = []
    for k, key in enumerate(keys):
        cut = tiles(realms[key])
        items.extend(cut)
        owner.extend([k] * len(cut))
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


def coverage_errors(realms: dict[str, MultiPolygon]) -> tuple[float, float]:
    """(gap, overlap) area in square degrees; both ~0 for a true partition."""
    covered = union(realms.values()).area
    return WORLD.area - covered, sum(g.area for g in realms.values()) - covered
