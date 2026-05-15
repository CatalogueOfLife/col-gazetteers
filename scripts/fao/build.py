"""Build the `fao` gazetteer from VLIZ MarineRegions FAO Major Fishing Areas.

Source: VLIZ GeoServer WFS, layer `MarineRegions:fao`. 19 Major Fishing Area
polygons (zone codes 18, 21, 27, 31, 34, 37, 41, 47, 48, 51, 57, 58, 61, 67,
71, 77, 81, 87, 88). Zone 88 (Pacific, Antarctic) is split across the
antimeridian and ships as two Features upstream — `split_features` merges
them into one MultiPolygon.

Scope: top-level only. CoL distributions that reference FAO subareas like
`37.4.1` (Mediterranean / Aegean / Crete) won't resolve here — those need a
separate upstream from FAO Fisheries Division directly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config
from common.download import download
from common.ids import assert_unique
from common.labels import write_labels
from common.manifest import write_manifest
from common.ogr import split_features, to_geojson

PREFIX = "fao"
SOURCE_URL = (
    "https://geo.vliz.be/geoserver/MarineRegions/ows"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeNames=MarineRegions:fao&outputFormat=application/json"
)
SOURCE_NAME = "MarineRegions:fao (FAO Major Fishing Areas via WFS)"
SOURCE_FILENAME = "fao.geojson"

ID_FIELD = "zone"  # the canonical FAO Major Fishing Area code
NAME_FIELD = "name"


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

    print(f"[{PREFIX}] target CRS: {config.epsg}, simplify {config.simplify_tolerance}")

    src_path, source_record = download(
        SOURCE_URL,
        SOURCES_DIR / PREFIX,
        role="wfs-geojson",
        name=SOURCE_NAME,
        filename=SOURCE_FILENAME,
        force=args.force,
    )
    print(f"[{PREFIX}] source: {src_path} ({source_record.size_bytes} bytes, md5={source_record.md5[:8]}…)")

    fc_path = work_dir / "all.geojson"
    to_geojson(src_path, fc_path, config)

    rows = split_features(
        fc_path, features_dir, id_field=ID_FIELD, name_field=NAME_FIELD,
    )
    assert_unique([r[0] for r in rows])
    label_count = write_labels(out_dir / "labels.tsv", rows)
    feature_count = len(list(features_dir.glob("*.geojson")))
    print(f"[{PREFIX}] split → {feature_count} features, {label_count} labels")

    write_manifest(
        out_dir / "build.json",
        prefix=PREFIX,
        config=config,
        sources=[source_record],
        feature_count=feature_count,
        label_count=label_count,
    )
    print(f"[{PREFIX}] manifest → {out_dir / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
