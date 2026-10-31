"""Build the `realm` gazetteer: 19 biogeographic realms, 8 terrestrial + 11 marine,
dividing the entire globe between them with no gaps and no overlaps.

Terrestrial source: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), the modern update
of Olson et al. 2001 WWF terrestrial ecoregions, hosted at
storage.googleapis.com/teow2016/Ecoregions2017.zip. The shapefile has 847
ecoregions with a `REALM` attribute, grouped here into the 8 realms enumerated
by the backend's `life.catalogue.api.vocab.area.BioGeoRealm` (Palearctic,
Nearctic, Afrotropic, Neotropic, Australasia, Indomalaya, Oceania, Antarctic).
The source uses two spellings that need rewriting to match the backend:
`Antarctica` → `Antarctic`, `Indomalayan` → `Indomalaya`.

Marine source: the 30 marine realms of Costello et al. 2017 (CC BY 4.0), the
same figshare download the `costello` gazetteer is built from. They are
grouped into 11 coarser realms (`MARINE_REALMS`), named after their MEOW
(Spalding et al. 2007) counterparts so the scheme reads like the familiar WWF
marine realms map. The grouping is ours and the geometry is Costello's
throughout. MEOW itself is not redistributable, so none of its boundaries are used.
Costello's realm 13 spans MEOW's Western and Central Indo-Pacific, so those
two are merged as `IndoWestPacific`. The offshore realms are folded into the
coastal realm they adjoin.

The two sources use different coastlines, and RESOLVE leaves out lakes and
"Rock and Ice" (N/A realm: the Greenland and Antarctic ice sheets, glaciers).
`realm.partition.partition_globe` reconciles them. Land wins over sea, and
lakes are land. Ice sheets and lakes on a realm border go to the land realm
they border most, so all of Greenland is Nearctic and the whole Antarctic
continent is Antarctic. Its floating ice shelves are sea: Costello already
counts the Ross and Ronne shelves in its Southern Ocean, and the smaller shelves
it leaves out go there too. Other coastline mismatches go to whichever
neighbour they border most. `realm.partition.as_coverage` then snaps all
realm borders together onto the output grid, so neighbouring realms share
each border vertex for vertex. The build fails unless the result covers
the globe exactly once.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config
from common.download import download
from common.ids import assert_unique
from common.labels import write_labels
from common.manifest import write_manifest
from common.ogr import split_features, to_geojson
from costello.build import SHAPEFILE as COSTELLO_SHAPEFILE
from costello.build import download_source as download_costello
from realm.partition import as_coverage, partition_globe, partition_problems

PREFIX = "realm"
SOURCE_URL = "https://storage.googleapis.com/teow2016/Ecoregions2017.zip"
SOURCE_NAME = "RESOLVE Ecoregions 2017 (Dinerstein et al.)"
SOURCE_FILENAME = "Ecoregions2017.zip"
UPSTREAM_VERSION = "2017"

# WWF REALM string → backend BioGeoRealm name (the id we write to disk).
REALM_MAP = {
    "Afrotropic":  "Afrotropic",
    "Antarctica":  "Antarctic",
    "Australasia": "Australasia",
    "Indomalayan": "Indomalaya",
    "Nearctic":    "Nearctic",
    "Neotropic":   "Neotropic",
    "Oceania":     "Oceania",
    "Palearctic":  "Palearctic",
}
REALM_FIELD = "realm"  # rewritten field name after our mapping pass

# Marine realm id → (label, Costello et al. 2017 realm numbers it groups).
MARINE_REALMS = {
    "Arctic":                    ("Arctic", (6,)),
    "TemperateNorthernAtlantic": ("Temperate Northern Atlantic", (1, 2, 3, 4, 5, 8, 18)),
    "TemperateNorthernPacific":  ("Temperate Northern Pacific", (7, 29)),
    "TropicalAtlantic":          ("Tropical Atlantic", (11, 21, 23)),
    "TropicalEasternPacific":    ("Tropical Eastern Pacific", (12, 22)),
    "CentralPacific":            ("Central Pacific", (9, 17)),
    "IndoWestPacific":           ("Indo-West Pacific", (13, 14, 16, 19, 20)),
    "TemperateSouthAmerica":     ("Temperate South America", (10, 24, 25)),
    "TemperateSouthernAfrica":   ("Temperate Southern Africa", (27,)),
    "TemperateAustralasia":      ("Temperate Australasia", (15, 26, 28)),
    "SouthernOcean":             ("Southern Ocean", (30,)),
}
COSTELLO_TO_REALM = {n: rid for rid, (_, ns) in MARINE_REALMS.items() for n in ns}
NAME_FIELD = "name"  # label; also what split_features stamps as properties.name
# Square degrees a realm may gain or lose when all borders are snapped together
# onto the output grid. Rounding moves far less; more means a misassigned piece.
COVERAGE_TOLERANCE = 0.01


def _features(path: Path):
    """Yield the features of a GeoJSON FeatureCollection with shapely geometries."""
    with path.open("r", encoding="utf-8") as f:
        for feat in json.load(f)["features"]:
            feat["geometry"] = shapely.make_valid(shape(feat["geometry"]))
            yield feat


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

    # The partition is computed in lon/lat whatever the target CRS; the final
    # realms are reprojected (if 3857 is asked for) only when writing.
    lonlat = config if config.crs == "4326" else load_config(cli_crs="4326")
    print(f"[{PREFIX}] target CRS: {config.epsg}, simplify {lonlat.simplify_tolerance}° before partitioning")

    zip_path, source_record = download(
        SOURCE_URL,
        SOURCES_DIR / PREFIX,
        role="shapefile",
        name=SOURCE_NAME,
        filename=SOURCE_FILENAME,
        force=args.force,
        upstream_version=UPSTREAM_VERSION,
    )
    print(f"[{PREFIX}] source: {zip_path} ({source_record.size_bytes:,} bytes, md5={source_record.md5[:8]}…)")

    # /vsizip/ + absolute archive path needs the double slash preserved, so
    # build it as a plain string — not a pathlib.Path.
    vsizip_src = f"/vsizip/{zip_path}/Ecoregions2017.shp"
    all_ecoregions = work_dir / "ecoregions.geojson"
    to_geojson(vsizip_src, all_ecoregions, lonlat)
    print(f"[{PREFIX}] ogr2ogr → {all_ecoregions}")

    # Group ecoregions by backend BioGeoRealm name. N/A is "Rock and Ice":
    # no realm, but still land, so it goes to the partition as unassigned land.
    land: dict[str, list] = defaultdict(list)
    rock_and_ice: list = []
    seen_unknown: set[str] = set()
    for feat in _features(all_ecoregions):
        wwf = feat["properties"].get("REALM", "")
        name = REALM_MAP.get(wwf)
        if name is not None:
            land[name].append(feat["geometry"])
        elif wwf == "N/A":
            rock_and_ice.append(feat["geometry"])
        else:
            seen_unknown.add(wwf)
    print(f"[{PREFIX}] {sum(map(len, land.values()))} ecoregions in {len(land)} land realms, "
          f"{len(rock_and_ice)} rock & ice")
    if seen_unknown:
        raise ValueError(f"unmapped REALM values: {sorted(seen_unknown)}")

    costello_zip, costello_record = download_costello(args.force)
    print(f"[{PREFIX}] marine source: {costello_zip} ({costello_record.size_bytes:,} bytes, md5={costello_record.md5[:8]}…)")
    costello_fc_path = work_dir / "costello.geojson"
    to_geojson(f"/vsizip/{costello_zip}/{COSTELLO_SHAPEFILE}", costello_fc_path, lonlat)
    sea: dict[str, list] = defaultdict(list)
    numbers = set()
    for feat in _features(costello_fc_path):
        number = int(feat["properties"]["Realm"])
        numbers.add(number)
        if number in COSTELLO_TO_REALM:
            sea[COSTELLO_TO_REALM[number]].append(feat["geometry"])
    if numbers != set(COSTELLO_TO_REALM):
        raise ValueError(
            f"Costello realms not matching COSTELLO_TO_REALM: "
            f"unmapped {sorted(numbers - set(COSTELLO_TO_REALM))}, "
            f"missing {sorted(set(COSTELLO_TO_REALM) - numbers)}"
        )
    print(f"[{PREFIX}] {len(numbers)} Costello realms in {len(sea)} marine realms")

    partition = partition_globe(
        dict(land), rock_and_ice, {k: sea[k] for k in MARINE_REALMS},
        north_polar_realm="Arctic", ice_shelf_realm="SouthernOcean",
        log=lambda msg: print(f"[{PREFIX}] {msg}"),
    )
    precision = 10 ** -lonlat.coord_precision
    realms, slivers = as_coverage(partition, precision)
    moved = {k: shapely.symmetric_difference(partition[k], realms[k]).area for k in realms}
    worst = max(moved, key=moved.__getitem__)
    print(f"[{PREFIX}] coverage: borders shared on a {precision:g}° grid, {slivers} slivers given to neighbours,"
          f" at most {moved[worst]:.6f} sq° moved ({worst})")
    if moved[worst] > COVERAGE_TOLERANCE:
        raise ValueError(f"snapping the realms to a shared grid moved {worst} by {moved[worst]} sq°")
    problems = partition_problems(realms)
    if problems:
        raise ValueError("realms do not partition the globe:\n  " + "\n  ".join(problems))

    features = []
    for rid, geom in realms.items():
        props = {REALM_FIELD: rid, NAME_FIELD: MARINE_REALMS[rid][0] if rid in MARINE_REALMS else rid}
        if rid in MARINE_REALMS:
            props["costello"] = list(MARINE_REALMS[rid][1])
        features.append({"type": "Feature", "properties": props, "geometry": mapping(geom)})
    by_realm = work_dir / "by_realm.geojson"
    with by_realm.open("w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)
    if config.crs != lonlat.crs:
        reprojected = work_dir / f"by_realm_{config.crs}.geojson"
        to_geojson(by_realm, reprojected, config)
        by_realm = reprojected

    rows = split_features(
        by_realm, features_dir,
        id_field=REALM_FIELD, name_field=NAME_FIELD,
    )
    assert_unique([r[0] for r in rows])
    label_count = write_labels(out_dir / "labels.tsv", rows)
    feature_count = len(list(features_dir.glob("*.geojson")))
    print(f"[{PREFIX}] {feature_count} realms, {label_count} labels")

    write_manifest(
        out_dir / "build.json",
        prefix=PREFIX,
        config=config,
        sources=[source_record, costello_record],
        feature_count=feature_count,
        label_count=label_count,
    )
    print(f"[{PREFIX}] manifest → {out_dir / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
