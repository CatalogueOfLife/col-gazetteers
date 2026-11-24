"""Refresh the OSM snapshot the iso build reads instead of querying Overpass.

Fetches, through Overpass, every relation tagged `ISO3166-2` for the countries
whose subdivisions the gazetteer lists, then the OSM relations Wikidata links
to ids no tagged relation covers. Writes them all, lightly simplified, to
`sources/iso/osm-snapshot.geojson.gz`. This takes hours when Overpass is busy,
which is why the monthly build doesn't do it: the `refresh-iso-osm.yml`
workflow runs it by hand or quarterly and puts the snapshot into the private
release the build downloads.
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
from iso import osm, universe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="ignore Overpass responses cached from an earlier run")
    parser.add_argument("--offline", action="store_true",
                        help="write the snapshot from cached Overpass responses only, without asking Overpass, "
                             "and list the countries it lacks")
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

    items: dict[int, tuple] = {}

    def read(paths: list[Path]) -> None:
        # One Overpass answer at a time, keeping only lightly simplified shapes, as all of them may not fit in memory.
        for path in paths:
            for rel_id, tags, geom in osm.relation_geoms(json.loads(path.read_text(encoding="utf-8"))):
                simple = shapely.simplify(geom, osm.SNAPSHOT_TOLERANCE, preserve_topology=True)
                items.setdefault(rel_id, (rel_id, tags, simple))

    if args.offline:
        read(osm.cached_answers(src / "osm"))
        covered = {code.split("-")[0] for code in osm.candidates_from(items.values())}
        missing = sorted(set(countries) - covered)
        count = osm.write_snapshot(items.values(), src / osm.SNAPSHOT)
        print(f"[iso] offline snapshot: {count} relations; no OSM answer yet for {len(missing)} countries: "
              f"{', '.join(missing)}", flush=True)
        return 0

    read(osm.fetch_osm(countries, [], src, force=args.force)[0])
    tagged = osm.candidates_from(items.values())
    linked = sorted({wd[i].osm_relation for i in subs if i not in tagged and i in wd and wd[i].osm_relation})
    print(f"[iso] {len(set(subs) & set(tagged))} ids have a tagged relation; fetching {len(linked)} linked ones "
          f"({time.monotonic() - started:.0f} s)", flush=True)
    read(osm.fetch_osm([], linked, src, force=args.force)[0])

    count = osm.write_snapshot(items.values(), src / osm.SNAPSHOT)
    size = (src / osm.SNAPSHOT).stat().st_size
    print(f"[iso] {count} relations in {src / osm.SNAPSHOT} ({size / 1e6:.0f} MB, {time.monotonic() - started:.0f} s)",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
