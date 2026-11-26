"""Refresh the OSM snapshot the iso build reads instead of querying for outlines.

By default it reads OSM's administrative areas from Overture Maps' latest
release (GeoParquet on a public S3 bucket, through DuckDB), matched to the ids
by code, by the OSM relation Wikidata links, or by Wikidata item; see
`overture.py`. With `--overpass` it asks Overpass instead, for every relation
tagged `ISO3166-2` and then the relations Wikidata links, which takes hours when
Overpass is busy and fails when it is down.

Writes the relations, lightly simplified, to `sources/iso/osm-snapshot.geojson.gz`.
The `refresh-iso-osm.yml` workflow runs it quarterly or by hand and puts the
snapshot into the private release the monthly build downloads.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shapely  # noqa: E402

from common.config import REPO_ROOT, SOURCES_DIR  # noqa: E402
import requests  # noqa: E402

from iso import osm, overture, universe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--overpass", action="store_true", help="ask Overpass instead of reading Overture")
    parser.add_argument("--release", help="the Overture release to read, such as 2026-11-18.0 (default: the latest)")
    parser.add_argument("--force", action="store_true", help="ignore Overpass responses cached from an earlier run")
    parser.add_argument("--offline", action="store_true",
                        help="with --overpass: write the snapshot from cached Overpass responses only, without "
                             "asking Overpass, and list the countries it lacks")
    args = parser.parse_args()
    started = time.monotonic()
    src = SOURCES_DIR / "iso"

    floor = universe.read_floor(REPO_ROOT / "iso" / "labels.tsv")
    wd, _ = universe.wikidata_codes(src, force=args.force)
    labels = universe.build_universe(floor, wd)
    aliases = universe.aliases_for(labels, wd)
    subs = sorted(i for i in labels if "-" in i and i not in aliases)
    countries = sorted({i.split("-")[0] for i in subs})
    print(f"[iso] {len(subs)} subdivisions in {len(countries)} countries", flush=True)

    if not args.overpass and not args.offline:
        release = args.release or overture.latest_release(
            requests.get(overture.LISTING, timeout=60, headers={"User-Agent": "col-gazetteers"}).text)
        codes = {i: (wd[i].osm_relation if i in wd else None, wd[i].item if i in wd else "") for i in subs}
        matches = overture.read_matches(release, codes)
        chosen = overture.choose(matches)
        print(f"[iso] Overture {release}: an OSM area for {len(chosen)} of {len(subs)} ids "
              f"({time.monotonic() - started:.0f} s)", flush=True)
        shapes = overture.read_shapes(release, {m.area for m in chosen.values()}, tolerance=osm.SNAPSHOT_TOLERANCE)
        found = overture.snapshot_items(chosen, shapes)
        count = osm.write_snapshot(found, src / osm.SNAPSHOT, countries=countries, source=f"overture {release}")
        size = (src / osm.SNAPSHOT).stat().st_size
        print(f"[iso] {count} relations in {src / osm.SNAPSHOT} ({size / 1e6:.0f} MB, "
              f"{time.monotonic() - started:.0f} s)", flush=True)
        return 0

    items: dict[int, tuple] = {}

    def read(paths: list[Path]) -> None:
        # One Overpass answer at a time, keeping only lightly simplified shapes, as all of them may not fit in memory.
        for path in paths:
            for rel_id, tags, geom in osm.relation_geoms(json.loads(path.read_text(encoding="utf-8"))):
                simple = shapely.simplify(geom, osm.SNAPSHOT_TOLERANCE, preserve_topology=True)
                items.setdefault(rel_id, (rel_id, tags, simple))

    if args.offline:
        read(osm.cached_answers(src / "osm"))
        covered = {cc for path in osm.cached_answers(src / "osm") if path.stem.startswith("countries_")
                   for cc in path.stem.removeprefix("countries_").split("-")}
        missing = sorted(set(countries) - covered)
        count = osm.write_snapshot(items.values(), src / osm.SNAPSHOT, countries=covered & set(countries))
        print(f"[iso] offline snapshot: {count} relations; no OSM answer yet for {len(missing)} countries: "
              f"{', '.join(missing)}", flush=True)
        return 0

    read(osm.fetch_osm(countries, [], src, force=args.force)[0])
    tagged = osm.candidates_from(items.values())
    linked = sorted({wd[i].osm_relation for i in subs if i not in tagged and i in wd and wd[i].osm_relation})
    print(f"[iso] {len(set(subs) & set(tagged))} ids have a tagged relation; fetching {len(linked)} linked ones "
          f"({time.monotonic() - started:.0f} s)", flush=True)
    read(osm.fetch_osm([], linked, src, force=args.force)[0])

    count = osm.write_snapshot(items.values(), src / osm.SNAPSHOT, countries=countries)
    size = (src / osm.SNAPSHOT).stat().st_size
    print(f"[iso] {count} relations in {src / osm.SNAPSHOT} ({size / 1e6:.0f} MB, {time.monotonic() - started:.0f} s)",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
