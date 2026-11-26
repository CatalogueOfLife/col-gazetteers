"""Subdivision outlines from OpenStreetMap, as Overture Maps publishes them.

Overture's divisions theme has OSM's administrative areas as GeoParquet in a
public S3 bucket, each with the OSM relation it comes from. Reading them takes
one download instead of hundreds of Overpass queries, which public Overpass
servers often refuse. An id finds its area by, in this order:

1. its code, for Overture's top-level regions, which carry their own ISO 3166-2 code;
2. the OSM relation its Wikidata item links to;
3. its Wikidata item, which Overture's divisions carry.

Only land areas whose shape comes from OSM are taken, so the snapshot stays keyed
by OSM relation, as the build expects.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import NamedTuple

import shapely
from shapely.geometry.base import BaseGeometry

from .geom import polys

BUCKET = "overturemaps-us-west-2"
LISTING = f"https://{BUCKET}.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/"
ROUTES = ("region", "osm", "wikidata")


class Match(NamedTuple):
    code: str
    route: str  # one of ROUTES
    area: str  # Overture division_area id
    rel: int  # the OSM relation the area's shape comes from
    admin_level: int | None
    name: str


def latest_release(listing: str) -> str:
    """The newest release named in the bucket's S3 listing (XML)."""
    releases = re.findall(r"<Prefix>release/([^/<]+)/</Prefix>", listing)
    if not releases:
        raise RuntimeError("no Overture release in the bucket listing")
    return max(releases)


def choose(matches: Iterable[Match]) -> dict[str, Match]:
    """One area per code: by route, then the higher level (lower admin_level), then area id."""
    best: dict[str, tuple[tuple, Match]] = {}
    for m in matches:
        rank = (ROUTES.index(m.route), m.admin_level if m.admin_level is not None else 99, m.area)
        if m.code not in best or rank < best[m.code][0]:
            best[m.code] = (rank, m)
    return {code: m for code, (_, m) in best.items()}


def snapshot_items(chosen: Mapping[str, Match], shapes: Mapping[str, BaseGeometry]) -> list[tuple[int, dict, BaseGeometry]]:
    """(relation, tags, geometry) for the snapshot, one per OSM relation, tagged with all its codes.
    An area whose shape is missing is left out."""
    from .osm import unwrap_antimeridian  # osm imports Overpass helpers this module doesn't need

    by_rel: dict[int, list[Match]] = {}
    for m in chosen.values():
        if m.area in shapes:
            by_rel.setdefault(m.rel, []).append(m)
    items = []
    for rel, ms in sorted(by_rel.items()):
        geom = unwrap_antimeridian(polys(shapes[ms[0].area]))
        if geom.is_empty:
            continue
        tags = {"ISO3166-2": ";".join(sorted(m.code for m in ms)), "boundary": "administrative", "name": ms[0].name}
        items.append((rel, tags, geom))
    return items


def _connect():
    import duckdb  # only the refresh needs it, not the build or its tests

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    return con


def _files(release: str, kind: str) -> str:
    return f"read_parquet('s3://{BUCKET}/release/{release}/theme=divisions/type={kind}/*.parquet', hive_partitioning=false)"


def read_matches(release: str, codes: Mapping[str, tuple[int | None, str]]) -> list[Match]:
    """Every OSM land area of `release` that matches a code. `codes`: code -> (OSM relation, Wikidata item)."""
    con = _connect()
    con.execute("CREATE TABLE ours (code VARCHAR, rel BIGINT, qid VARCHAR)")
    con.executemany("INSERT INTO ours VALUES (?, ?, ?)", [(c, rel, qid or None) for c, (rel, qid) in codes.items()])
    con.execute(f"""
        CREATE TABLE a AS SELECT id, division_id, subtype, admin_level, region, name,
            TRY_CAST(regexp_extract(s.record_id, '^r(\\d+)', 1) AS BIGINT) AS rel
        FROM (SELECT id, division_id, subtype, admin_level, region, names."primary" AS name,
                     unnest(list_filter(sources, lambda x: x.dataset = 'OpenStreetMap')) AS s
              FROM {_files(release, 'division_area')} WHERE class = 'land')""")
    con.execute(f"CREATE TABLE d AS SELECT id, wikidata FROM {_files(release, 'division')} WHERE wikidata IS NOT NULL")
    rows = con.execute("""
        SELECT o.code, 'region', a.id, a.rel, a.admin_level, a.name FROM a JOIN ours o ON a.subtype = 'region' AND a.region = o.code
        WHERE a.rel IS NOT NULL
        UNION ALL
        SELECT o.code, 'osm', a.id, a.rel, a.admin_level, a.name FROM a JOIN ours o ON a.rel = o.rel
        UNION ALL
        SELECT o.code, 'wikidata', a.id, a.rel, a.admin_level, a.name FROM ours o JOIN d ON d.wikidata = o.qid
            JOIN a ON a.division_id = d.id WHERE a.rel IS NOT NULL""").fetchall()
    return [Match(*row) for row in rows]


def read_shapes(release: str, areas: Iterable[str], *, tolerance: float) -> dict[str, BaseGeometry]:
    """area id -> its shape, simplified by `tolerance` degrees as it is read."""
    con = _connect()
    con.execute("CREATE TABLE want (id VARCHAR)")
    con.executemany("INSERT INTO want VALUES (?)", [(a,) for a in sorted(set(areas))])
    cursor = con.execute(f"SELECT id, ST_AsWKB(geometry) FROM {_files(release, 'division_area')} SEMI JOIN want USING (id)")
    shapes = {}
    while batch := cursor.fetchmany(500):
        for area, wkb in batch:
            geom = shapely.from_wkb(bytes(wkb))
            shapes[area] = shapely.simplify(geom, tolerance, preserve_topology=True)
    return shapes
