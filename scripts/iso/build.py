"""Build the iso gazetteer: ISO 3166 countries as GBIF defines them, and their subdivisions.

Countries are the VLIZ EEZ-land union v4 with GBIF's changes replayed, each as a
land shape (`iso/features/`) and a land + EEZ shape (`iso/eez/features/`).
Subdivisions come from OpenStreetMap first, then Wikidata-linked OSM relations,
Natural Earth, geoBoundaries, unions of other shapes, and placeholder circles,
fitted into their country. Every id of the last committed labels.tsv is kept.

See docs/superpowers/specs/2026-11-24-iso-gbif-design.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shapely  # noqa: E402

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config  # noqa: E402
from common.manifest import write_manifest  # noqa: E402
from iso import (  # noqa: E402
    checks, countries, derived, fit, gbif_drift, gbif_sources, geoboundaries, natural_earth, osm, placeholder,
    political, universe, write,
)
from iso.geom import polys  # noqa: E402
from iso.model import Candidate  # noqa: E402

PREFIX = "iso"
HISTORIC_PARENTS = {"CS": ("RS", "ME", "XK"), **countries.HISTORIC}


def log(message: str) -> None:
    print(f"[{PREFIX}] {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="refetch every source")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--allow-partial-osm", action="store_true",
                        help="accept an OSM snapshot that lacks some countries (for test builds only)")
    args = parser.parse_args()
    started = time.monotonic()
    config = load_config()
    write.require_degrees(config)
    iso_dir, src, work = REPO_ROOT / PREFIX, SOURCES_DIR / PREFIX, WORK_DIR / PREFIX
    work.mkdir(parents=True, exist_ok=True)

    # The floor, before anything writes to iso/.
    floor = universe.read_floor(iso_dir / "labels.tsv")
    for warning in gbif_drift.check_drift():
        print(f"::warning::{warning}", flush=True)

    # Countries.
    union_zip, eez_gpkg, records = gbif_sources.locate_gbif_files(src, force=args.force)
    rows = political.read_rows(union_zip, work)
    gbif_osm, recs = political.fetch_osm_relations(political.GBIF_OSM_RELATIONS, src, force=args.force)
    records += recs
    missing = political.missing_targets(rows, gbif_osm)
    if missing:
        raise RuntimeError(f"GBIF's changes would miss {', '.join(missing)}: see gbif_import_political.sh")
    rows = political.apply_gbif_changes(rows, gbif_osm)
    iso_map, rec = political.iso3_to_iso2(src, force=args.force)
    records.append(rec)
    unmapped = political.assign_codes(rows, iso_map)
    if unmapped:
        raise RuntimeError(f"GBIF's country list has no alpha-2 code for {sorted(unmapped)}")
    water = countries.read_water(eez_gpkg, work)
    world = countries.build_countries(rows, water, workers=args.workers)
    countries.add_historic(world)
    log(f"{len(rows)} GBIF rows → {len(world.land)} countries ({time.monotonic() - started:.0f} s)")

    # Ids, labels and aliases.
    wd, rec = universe.wikidata_codes(src, force=args.force)
    records.append(rec)
    labels = universe.build_universe(floor, wd)
    aliases = universe.aliases_for(labels, wd)
    dual = {code: target for code, target in aliases.items() if len(target) == 2}
    subs = sorted(i for i in labels if "-" in i and i not in aliases)
    log(f"{len(labels)} ids ({len(labels) - len(floor)} new), {len(subs)} subdivisions to fit, {len(aliases)} aliases")

    # Candidates. OSM comes from the snapshot refresh_osm.py writes, never from Overpass directly.
    snapshot = src / osm.SNAPSHOT
    if not snapshot.is_file():
        raise RuntimeError(
            f"{snapshot} is missing. Run scripts/iso/refresh_osm.py, or take the last one: "
            f"gh release download osm-iso-snapshot -R {gbif_sources.RELEASE_REPO} -D {src} -p {osm.SNAPSHOT}")
    missing = osm.snapshot_missing(snapshot, {i.split("-")[0] for i in subs})
    if missing and not args.allow_partial_osm:
        raise RuntimeError(f"the OSM snapshot has no Overpass answer for {len(missing)} countries: {', '.join(missing)}")
    records.append(gbif_sources.local_record(
        snapshot, "osm-snapshot", "OSM relations tagged ISO3166-2 or linked from Wikidata, via Overpass",
        f"release osm-iso-snapshot of {gbif_sources.RELEASE_REPO}", "snapshot"))
    items = osm.read_snapshot(snapshot)
    tagged = osm.candidates_from(items)
    by_rel = {rel_id: geom for rel_id, _, geom in items}
    linked = {i: wd[i].osm_relation for i in subs if i not in tagged and i in wd and wd[i].osm_relation}
    via_wikidata = {i: Candidate(by_rel[r], f"osm-relation-{r}", "osm-wikidata", note="OSM relation linked from Wikidata")
                    for i, r in linked.items() if r in by_rel}
    ne, rec = natural_earth.natural_earth_candidates(src, work, force=args.force)
    records.append(rec)
    wanted = {i: labels[i] for i in subs if i not in tagged and i not in via_wikidata and i not in ne}
    gb, recs = geoboundaries.geoboundaries_candidates(wanted, src, iso3={v: k for k, v in iso_map.items()},
                                                      force=args.force)
    records += recs
    log(f"candidates: OSM {len(tagged)}, via Wikidata {len(via_wikidata)}, Natural Earth {len(ne)}, "
        f"geoBoundaries {len(gb)} ({time.monotonic() - started:.0f} s)")

    # A withdrawn code links to its one successor only when no source has a shape of its own.
    shaped = {i for i in subs if i in tagged or i in via_wikidata or i in ne or i in gb or i in derived.DERIVED}
    links = universe.superseded(wd, set(labels), shaped=shaped)
    aliases.update(links)
    subs = [i for i in subs if i not in links]
    log(f"{len(links)} withdrawn codes without a shape link to their successor")

    # Fitting. Derived ids come last, as they are unions of fitted shapes.
    areas: dict[str, tuple[set[str], object, object]] = {}

    def area_of(parent: str):
        if parent not in areas:
            codes = set(HISTORIC_PARENTS.get(parent, ())) or ({parent} | fit.territories(parent, dual))
            land = polys(shapely.union_all([world.land[c] for c in codes if c in world.land]))
            eez = polys(shapely.union_all([world.eez[c] for c in codes if c in world.eez]))
            areas[parent] = (codes, land, eez)
        return areas[parent]

    fitted = {}

    def fit_one(code: str, candidates: list[Candidate]) -> None:
        parent = code.split("-")[0]
        codes, land, eez = area_of(parent)
        candidates.append(placeholder.placeholder(code, wd.get(code), world.land.get(parent, land)))
        result = fit.fit(candidates, land, eez, world.eez)
        if result.absorb:
            fit.absorb(world, codes, result.geom)
            areas.pop(parent, None)
        fitted[code] = result

    for code in subs:
        if code not in derived.DERIVED:
            fit_one(code, [c for c in (tagged.get(code), via_wikidata.get(code), ne.get(code), gb.get(code)) if c])
    made = derived.derive({**world.land, **{k: r.geom for k, r in fitted.items()}})
    for code in subs:
        if code in derived.DERIVED:
            fit_one(code, [c for c in (tagged.get(code), via_wikidata.get(code), made.get(code)) if c])
    log(f"fitted {len(fitted)} subdivisions ({time.monotonic() - started:.0f} s)")

    # Writing and checking.
    labels = write.settle_labels(labels, floor, fitted)
    feature_count, label_count = write.write_tree(iso_dir, world, fitted, labels, aliases, config)
    manifest = iso_dir / "build.json"
    write_manifest(manifest, prefix=PREFIX, sources=records, feature_count=feature_count,
                   label_count=label_count, config=config)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["gbif_import_sh_commit"] = gbif_drift.PINNED_COMMIT
    manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    problems = checks.check_tree(iso_dir, set(floor))
    if problems:
        raise RuntimeError(f"{len(problems)} problems in the written tree:\n  " + "\n  ".join(problems[:50]))

    modes = {}
    for r in fitted.values():
        modes[r.mode] = modes.get(r.mode, 0) + 1
    real = sum(1 for r in fitted.values() if r.candidate.resolution not in fit.PLACEHOLDERS)
    conflicts = sorted(code for code, r in fitted.items() if r.mode == "conflict")
    log(f"{label_count} ids, {feature_count} features; {len(world.land)} countries in both sets; "
        f"{real} real outlines, {len(fitted) - real} circles; fit {modes}; conflicts {conflicts} "
        f"({time.monotonic() - started:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
