"""Build the `longhurst` gazetteer from VLIZ MarineRegions Longhurst Provinces.

Source: VLIZ GeoServer WFS, layer `MarineRegions:longhurst`. 54 biogeographical
province polygons keyed by the canonical 4-letter Longhurst code (`provcode`,
e.g. `FKLD` = SW Atlantic Shelves, `NADR` = North Atlantic Drift). Name comes
from `provdescr`.

Longhurst labels are bundled in the backend's api module, so labels.tsv here
is technically optional — we emit it anyway as a fallback / cross-check.
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

PREFIX = "longhurst"
SOURCE_URL = (
    "https://geo.vliz.be/geoserver/MarineRegions/ows"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeNames=MarineRegions:longhurst&outputFormat=application/json"
)
SOURCE_NAME = "MarineRegions:longhurst (Longhurst Provinces via WFS)"
SOURCE_FILENAME = "longhurst.geojson"

ID_FIELD = "provcode"
NAME_FIELD = "provdescr"


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
