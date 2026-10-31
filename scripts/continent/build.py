"""Build the `continent` gazetteer: the 7 continents, as land only.

It follows the 7 continent model of the backend's former `Continent` enum, with
North America including the Caribbean and reaching down to and including Panama.

The base is Natural Earth's map subunits grouped by their CONTINENT field, which
follows country lines. Where a physical boundary runs through a country, the
country is cut along it:

  * Europe/Asia: the Ural divide, the Ural River, the Caspian, the Greater
    Caucasus divide, the Black Sea, the Turkish Straits and the Aegean. This
    divides Russia, Kazakhstan, Georgia, Azerbaijan and Turkey.
  * Africa/Asia: the Suez Canal and the Red Sea, so the Sinai is Asia.
  * Asia/Oceania: the Lydekker line, the edge of the Sahul shelf, so all of New
    Guinea and the Aru Islands are Oceania, and the Moluccas are Asia.
  * Socotra sits on the Somali plate and is Africa, not Asia like the rest of Yemen.

The divides come from scripts/continent/ridges.geojson, traced on the Copernicus
DEM by trace_ridges.py; the rivers are Natural Earth's. Vertices at sea were placed
by hand. The build fails if one of them runs over land, or if a land boundary
crosses a country it doesn't cut.

Natural Earth leaves 23 remote island groups in "Seven seas (open ocean)" and
places Trinidad and Tobago in the Caribbean. `SUBUNIT_CONTINENTS` assigns them.

Continents stop at the coast and don't divide up the globe. Neighbouring ones
share their borders vertex for vertex, and are simplified together.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config  # noqa: E402
from common.download import download  # noqa: E402
from common.ids import assert_unique  # noqa: E402
from common.labels import write_labels  # noqa: E402
from common.manifest import write_manifest  # noqa: E402
from common.ogr import split_features, to_geojson  # noqa: E402
from realm.partition import polygonal, union  # noqa: E402

PREFIX = "continent"
NE_BASE = "https://naciscdn.org/naturalearth/10m"
SUBUNITS_FILE = "ne_10m_admin_0_map_subunits"
RIVERS_FILE = "ne_10m_rivers_lake_centerlines"
RIDGES = Path(__file__).resolve().parent / "ridges.geojson"

AFRICA, ANTARCTICA, ASIA, EUROPE = "Africa", "Antarctica", "Asia", "Europe"
NORTH_AMERICA, OCEANIA, SOUTH_AMERICA = "NorthAmerica", "Oceania", "SouthAmerica"
LABELS = {
    AFRICA: "Africa",
    ANTARCTICA: "Antarctica",
    ASIA: "Asia",
    EUROPE: "Europe",
    NORTH_AMERICA: "North America",
    OCEANIA: "Oceania",
    SOUTH_AMERICA: "South America",
}
NE_CONTINENTS = {label: cid for cid, label in LABELS.items()}
SEVEN_SEAS = "Seven seas (open ocean)"

# Natural Earth subunit NAME → continent, taking precedence over its CONTINENT.
SUBUNIT_CONTINENTS = {
    "Seychelles": AFRICA,
    "Mauritius": AFRICA,
    "Réunion": AFRICA,
    "St. Helena": AFRICA,
    "Ascension": AFRICA,
    "Tristan da Cunha": AFRICA,
    "Prince Edward Is.": AFRICA,
    "Tromelin I.": AFRICA,
    "Glorioso Is.": AFRICA,
    "Maldives": ASIA,
    "Andaman Is.": ASIA,
    "Nicobar Is.": ASIA,
    "Lakshadweep": ASIA,
    "Br. Indian Ocean Ter.": ASIA,
    "Diego Garcia NSF": ASIA,
    "Azores": EUROPE,
    "Clipperton I.": NORTH_AMERICA,
    "Isla Sala y Gomez": OCEANIA,
    "S. Georgia": ANTARCTICA,
    "S. Sandwich Is.": ANTARCTICA,
    "S. Orkney Is.": ANTARCTICA,
    "Heard I. and McDonald Is.": ANTARCTICA,
    "Fr. S. Antarctic Lands": ANTARCTICA,
    # On the South American shelf, a continuation of the Venezuelan coastal range.
    "Trinidad": SOUTH_AMERICA,
    "Tobago": SOUTH_AMERICA,
}

# The countries each boundary cuts, by ISO_A2_EH. Natural Earth's two Russia
# subunits follow oblast borders, so they are recut along with the rest.
EUROPE_ASIA_ISO = {"RU", "KZ", "GE", "AZ"}
# Natural Earth has Thrace and Anatolia as separate polygons, so Turkey is divided
# polygon by polygon. The straits are far narrower than the vertices here are exact.
TURKEY_ISO = "TR"
EGYPT_ISO = "EG"
INDONESIA_ISO = "ID"
YEMEN_ISO = "YE"

# Vertices at sea (lon, lat), placed by hand. Each is checked to stay off land.
# East of Novaya Zemlya and Franz Josef Land, west of Yamal and Bely Island.
KARA_SEA = [(70.0, 89.9), (70.0, 78.0), (70.5, 76.2), (68.0, 75.0), (66.0, 73.0), (66.0, 71.5), (66.3, 70.0)]
# The Ural River's mouth to the Greater Caucasus, west of the Mangyshlak peninsula.
CASPIAN_SEA = [(51.7, 46.5), (51.0, 46.2), (49.6, 45.0), (49.5, 43.0), (50.3, 41.8), (49.7, 40.9)]
# The Greater Caucasus to the Bosphorus, off the Crimea and the Turkish coast.
BLACK_SEA = [(36.9, 44.7), (35.0, 43.2), (30.0, 42.0), (29.13, 41.25)]
# Roughly the middle of the Bosphorus, Marmara and the Dardanelles, then out
# between Imbros and Tenedos.
TURKISH_STRAITS = [
    (29.08, 41.15), (29.0, 40.95), (28.0, 40.8), (27.4, 40.72), (26.9, 40.5),
    (26.55, 40.25), (26.2, 40.03), (25.9, 39.97), (25.5, 39.9),
]
# The Mediterranean off Port Said, to the north end of the canal.
MEDITERRANEAN = [(32.32, 31.6)]
# The Gulf of Suez, the Strait of Jubal and the Red Sea, east of Egypt's islands.
RED_SEA = [
    (32.56, 29.85), (32.65, 29.5), (32.85, 29.1), (33.2, 28.5), (33.42, 28.1), (33.85, 27.8),
    (34.12, 27.62), (34.4, 27.2), (36.2, 25.5), (38.0, 23.0),
]
# The Lydekker line, north to south: Waigeo and Misool on the Sahul side, Seram
# and Goram off it, then between the Kai and Aru Islands, and past Tanimbar.
LYDEKKER_LINE = [
    (129.65, 2.5), (129.62, -0.5), (129.5, -1.3), (129.4, -2.1), (130.0, -2.55), (131.5, -2.75),
    (131.75, -3.2), (132.2, -4.2), (133.6, -5.2), (133.6, -7.5), (132.5, -9.0), (132.5, -12.0),
]
SOCOTRA = box(52.0, 11.5, 55.0, 13.0)

# Land a sea line may cross at each end, where it meets a line that ends on land:
# the Ural delta, and the last few cells of a divide at the coast.
SEA_LINE_LANDFALL = 0.25
# Square degrees a continent may gain or lose when borders are snapped to the grid.
SNAP_TOLERANCE = 0.01


def _join(tail: np.ndarray, head: np.ndarray, window: int = 6) -> np.ndarray:
    """`tail` followed by `head`. River segments often overshoot each other where
    they meet, which would cross the line over itself, so cut them where they cross."""
    last = len(tail) - 1
    for i in range(last - 1, max(last - window, -1), -1):
        a = LineString(tail[i:i + 2])
        for j in range(min(window, len(head) - 1)):
            crossing = a.intersection(LineString(head[j:j + 2]))
            if crossing.geom_type == "Point":
                return np.concatenate([tail[:i + 1], [[crossing.x, crossing.y]], head[j + 1:]])
    return np.concatenate([tail, head])


def stitch(lines: Iterable[LineString], start: Sequence[float]) -> LineString:
    """Join river segments into one line, walking from the end nearest `start`.

    Upstream data draws a river through a reservoir as a separate feature, not
    always in the same direction. Each next segment is the one with the nearest end.
    """
    remaining = [np.asarray(line.coords)[:, :2] for line in lines]
    if not remaining:
        raise ValueError("nothing to stitch")
    out: np.ndarray | None = None
    at = np.asarray(start[:2])
    while remaining:
        ends = []
        for i, coords in enumerate(remaining):
            ends.append((float(np.hypot(*(coords[0] - at))), i, False))
            ends.append((float(np.hypot(*(coords[-1] - at))), i, True))
        _, i, flip = min(ends)
        coords = remaining.pop(i)
        coords = coords[::-1] if flip else coords
        out = coords if out is None else _join(out, coords)
        at = out[-1]
    line = LineString(out)
    if not line.is_simple:
        raise ValueError(f"stitched line crosses itself, starting at {line.coords[0]}")
    return line


def closed_polygon(*vertices: Iterable[Sequence[float]]) -> Polygon:
    """One polygon from runs of vertices. It must not cross itself: a mistake in
    the order or in a hand-placed vertex would otherwise cut the wrong land."""
    ring = [tuple(v[:2]) for run in vertices for v in run]
    polygon = Polygon(ring)
    if not polygon.is_valid:
        raise ValueError(f"boundary polygon is invalid: {shapely.is_valid_reason(polygon)}")
    return polygon


def europe_side(ural_crest: LineString, ural_river: LineString, caucasus_crest: LineString) -> Polygon:
    """Everything on the European side, closed around the Atlantic. `ural_crest`
    runs north to south, `ural_river` to its mouth, `caucasus_crest` east to west."""
    return closed_polygon(
        KARA_SEA, ural_crest.coords, ural_river.coords, CASPIAN_SEA,
        caucasus_crest.coords, BLACK_SEA, TURKISH_STRAITS,
        [(20.0, 30.0), (-40.0, 30.0), (-40.0, 89.9)],
    )


def sinai_side(suez_canal: LineString) -> Polygon:
    """Egypt east of the Suez Canal and the Red Sea. `suez_canal` runs north to south."""
    return closed_polygon(MEDITERRANEAN, suez_canal.coords, RED_SEA, [(38.0, 31.6)])


def sahul_side() -> Polygon:
    """Indonesia east of the Lydekker line."""
    return closed_polygon(LYDEKKER_LINE, [(142.0, -12.0), (142.0, 2.5)])


def land_crossed(sea_line: Sequence[Sequence[float]], land: BaseGeometry) -> float:
    """Degrees of `sea_line` that run over `land`."""
    return shapely.intersection(LineString(sea_line), land).length


def split_by_parts(geom: BaseGeometry, side: Polygon) -> tuple[list[Polygon], list[Polygon]]:
    """The polygons of `geom` inside `side` and those outside, none of them cut."""
    inside: list[Polygon] = []
    outside: list[Polygon] = []
    for part in polygonal(geom).geoms:
        (inside if side.contains(part.representative_point()) else outside).append(part)
    return inside, outside


def assign_subunit(name: str, ne_continent: str) -> str:
    """The continent of a Natural Earth subunit, before any cuts."""
    if name in SUBUNIT_CONTINENTS:
        return SUBUNIT_CONTINENTS[name]
    if ne_continent == SEVEN_SEAS or ne_continent not in NE_CONTINENTS:
        raise ValueError(f"subunit {name!r} in {ne_continent!r} has no continent")
    return NE_CONTINENTS[ne_continent]


def share_borders(
    continents: Mapping[str, BaseGeometry], grid_size: float
) -> tuple[dict[str, MultiPolygon], int]:
    """`continents` redrawn on a `grid_size` grid, neighbours sharing their borders.

    The land counterpart of `realm.partition.as_coverage`: all outlines are
    snap-rounded together into one network of edges, which cuts the world into
    faces. A face goes to the continent its interior lies in. Faces in no continent
    are sea or lakes, and dropped. Faces in two are slivers along a shared border,
    and go to the first of them. Returns (continents, number of slivers).
    """
    keys = list(continents)
    geoms = [continents[k] for k in keys]
    edges = shapely.union_all(list(shapely.boundary(geoms)), grid_size=grid_size)
    faces = list(shapely.get_parts(shapely.polygonize(shapely.get_parts(edges))))
    face, continent = STRtree(geoms).query(shapely.point_on_surface(faces), predicate="within")
    parts: dict[str, list[Polygon]] = defaultdict(list)
    taken: set[int] = set()
    slivers = 0
    for f, c in sorted(zip(face.tolist(), continent.tolist())):
        if f in taken:
            slivers += 1
            continue
        taken.add(f)
        parts[keys[c]].append(faces[f])
    return {k: polygonal(shapely.coverage_union_all(parts[k])) for k in keys}, slivers


def round_coords(geom: BaseGeometry, decimals: int) -> BaseGeometry:
    return shapely.transform(geom, lambda coords: np.round(coords, decimals))


def read_features(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)["features"]


def read_ridges(path: Path) -> dict[str, LineString]:
    return {f["properties"]["name"]: LineString(f["geometry"]["coordinates"]) for f in read_features(path)}


def read_lines(path: Path) -> list[LineString]:
    lines: list[LineString] = []
    for feature in read_features(path):
        lines.extend(shapely.get_parts(shape(feature["geometry"])))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crs", choices=["4326", "3857"], default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = load_config(cli_crs=args.crs)
    out_dir = REPO_ROOT / PREFIX
    features_dir = out_dir / "features"
    work_dir = WORK_DIR / PREFIX
    work_dir.mkdir(parents=True, exist_ok=True)

    # Cut in lon/lat whatever the target CRS, reproject when writing. Sources are
    # read unsimplified: simplifying country by country would open gaps between them.
    lonlat = config if config.crs == "4326" else load_config(cli_crs="4326")
    tolerance = lonlat.simplify_tolerance
    grid_size = 10 ** -lonlat.coord_precision
    raw = dataclasses.replace(lonlat, simplify_tolerance=0)
    print(f"[{PREFIX}] target CRS: {config.epsg}, simplify {tolerance}°")

    subunits_zip, subunits_record = download(
        f"{NE_BASE}/cultural/{SUBUNITS_FILE}.zip", SOURCES_DIR / PREFIX,
        role="shapefile", name="Natural Earth 10m map subunits", force=args.force,
    )
    rivers_zip, rivers_record = download(
        f"{NE_BASE}/physical/{RIVERS_FILE}.zip", SOURCES_DIR / PREFIX,
        role="shapefile", name="Natural Earth 10m rivers and lake centerlines", force=args.force,
    )

    subunits_path = work_dir / "subunits.geojson"
    to_geojson(f"/vsizip/{subunits_zip}/{SUBUNITS_FILE}.shp", subunits_path, raw,
               select=["NAME", "ISO_A2_EH", "CONTINENT"], rfc7946=False)
    rivers_path = {}
    for river in ("Ural", "Suez Canal"):
        rivers_path[river] = work_dir / f"{river.replace(' ', '_')}.geojson"
        to_geojson(f"/vsizip/{rivers_zip}/{RIVERS_FILE}.shp", rivers_path[river], raw,
                   where=f"name = '{river}'", select=["name"])

    ridges = read_ridges(RIDGES)
    ural_crest, caucasus_crest = ridges["ural-crest"], ridges["caucasus-crest"]
    ural_river = stitch(read_lines(rivers_path["Ural"]), ural_crest.coords[-1])
    suez_canal = stitch(read_lines(rivers_path["Suez Canal"]), MEDITERRANEAN[-1])
    print(f"[{PREFIX}] Ural River {ural_river.length:.1f}°, Suez Canal {suez_canal.length:.1f}°")

    europe = europe_side(ural_crest, ural_river, caucasus_crest)
    sinai = sinai_side(suez_canal)
    sahul = sahul_side()

    pieces: dict[str, list] = defaultdict(list)
    by_iso: dict[str, list[BaseGeometry]] = defaultdict(list)
    land_only: dict[str, list[tuple[str, BaseGeometry]]] = defaultdict(list)
    for feature in read_features(subunits_path):
        props = feature["properties"]
        geom = polygonal(shapely.make_valid(shape(feature["geometry"])))
        name, iso = props["NAME"], props["ISO_A2_EH"]
        cid = assign_subunit(name, props["CONTINENT"])
        land_only[iso].append((name, geom))
        if iso in EUROPE_ASIA_ISO:
            by_iso["europe-asia"].append(geom)
        elif iso == TURKEY_ISO:
            by_iso["turkey"].append(geom)
        elif iso == EGYPT_ISO:
            by_iso["egypt"].append(geom)
        elif iso == INDONESIA_ISO:
            by_iso["indonesia"].append(geom)
        elif iso == YEMEN_ISO:
            by_iso["yemen"].append(geom)
        else:
            pieces[cid].append(geom)

    # Sea lines must stay at sea, or a cut would run through land by mistake.
    cut_land = union(by_iso["europe-asia"])
    sea_lines = {
        "Kara Sea": (KARA_SEA + [ural_crest.coords[0]], cut_land),
        "Caspian Sea": ([ural_river.coords[-1]] + CASPIAN_SEA + [caucasus_crest.coords[0]], cut_land),
        "Black Sea": ([caucasus_crest.coords[-1]] + BLACK_SEA, union([cut_land, *by_iso["turkey"]])),
        "Red Sea": ([suez_canal.coords[-1]] + RED_SEA, union(by_iso["egypt"])),
        "Lydekker line": (LYDEKKER_LINE, union(by_iso["indonesia"])),
    }
    for name, (line, land) in sea_lines.items():
        crossed = land_crossed(line, land)
        print(f"[{PREFIX}] {name} crosses {crossed:.3f}° of land")
        if crossed > SEA_LINE_LANDFALL or (name == "Lydekker line" and crossed > 0):
            hits = shapely.intersection(LineString(line), land)
            raise ValueError(f"{name} runs over land: {crossed:.3f}° at {hits.bounds}")

    # A land boundary crossing a country outside the lists above would leave that
    # country uncut. The countries it borders only touch it along their own border.
    land_lines = {
        "Ural divide": ural_crest, "Ural River": ural_river,
        "Caucasus divide": caucasus_crest, "Suez Canal": suez_canal,
    }
    handled = {*EUROPE_ASIA_ISO, EGYPT_ISO}
    for name, line in land_lines.items():
        for iso, geoms in land_only.items():
            if iso in handled:
                continue
            for subunit, geom in geoms:
                crossed = shapely.intersection(line, geom).length
                if crossed > SEA_LINE_LANDFALL:
                    raise ValueError(f"{name} crosses {subunit} ({iso}) for {crossed:.2f}°")

    land = union(by_iso["europe-asia"])
    pieces[EUROPE].append(polygonal(land.intersection(europe)))
    pieces[ASIA].append(polygonal(land.difference(europe)))
    thrace, anatolia = split_by_parts(union(by_iso["turkey"]), europe)
    pieces[EUROPE].extend(thrace)
    pieces[ASIA].extend(anatolia)
    print(f"[{PREFIX}] Turkey: {len(thrace)} polygons in Europe, {len(anatolia)} in Asia")

    egypt = union(by_iso["egypt"])
    pieces[ASIA].append(polygonal(egypt.intersection(sinai)))
    pieces[AFRICA].append(polygonal(egypt.difference(sinai)))
    indonesia = union(by_iso["indonesia"])
    pieces[OCEANIA].append(polygonal(indonesia.intersection(sahul)))
    pieces[ASIA].append(polygonal(indonesia.difference(sahul)))
    yemen = union(by_iso["yemen"])
    pieces[AFRICA].append(polygonal(yemen.intersection(SOCOTRA)))
    pieces[ASIA].append(polygonal(yemen.difference(SOCOTRA)))

    dissolved = {cid: union(pieces[cid]) for cid in LABELS}
    continents, slivers = share_borders(dissolved, grid_size)
    moved = {k: shapely.symmetric_difference(dissolved[k], continents[k]).area for k in LABELS}
    worst = max(moved, key=moved.__getitem__)
    print(f"[{PREFIX}] borders shared on a {grid_size:g}° grid, {slivers} slivers,"
          f" at most {moved[worst]:.6f} sq° moved ({worst})")
    if moved[worst] > SNAP_TOLERANCE:
        raise ValueError(f"snapping moved {worst} by {moved[worst]} sq°")

    keys = list(continents)
    simplified = shapely.coverage_simplify([continents[k] for k in keys], tolerance)
    rounded = {k: round_coords(g, lonlat.coord_precision) for k, g in zip(keys, simplified)}
    for cid, geom in rounded.items():
        if not geom.is_valid:
            raise ValueError(f"{cid}: {shapely.is_valid_reason(geom)}")
        print(f"[{PREFIX}] {cid}: {len(shapely.get_parts(geom)):,} polygons, "
              f"{shapely.get_num_coordinates(geom):,} vertices")

    features = [
        {"type": "Feature", "properties": {"id": cid, "name": LABELS[cid]}, "geometry": mapping(rounded[cid])}
        for cid in sorted(rounded)
    ]
    by_continent = work_dir / "by_continent.geojson"
    with by_continent.open("w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)
    if config.crs != lonlat.crs:
        reprojected = work_dir / f"by_continent_{config.crs}.geojson"
        to_geojson(by_continent, reprojected, config)
        by_continent = reprojected

    rows = split_features(by_continent, features_dir, id_field="id", name_field="name")
    assert_unique([r[0] for r in rows])
    label_count = write_labels(out_dir / "labels.tsv", rows)
    feature_count = len(list(features_dir.glob("*.geojson")))
    print(f"[{PREFIX}] {feature_count} continents, {label_count} labels")

    write_manifest(
        out_dir / "build.json",
        prefix=PREFIX,
        config=config,
        sources=[subunits_record, rivers_record],
        feature_count=feature_count,
        label_count=label_count,
    )
    print(f"[{PREFIX}] manifest → {out_dir / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
