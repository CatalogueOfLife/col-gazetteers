"""Build the `teow` gazetteer — Terrestrial Ecoregions of the World.

Source: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), the modern update
of Olson et al. 2001 WWF terrestrial ecoregions (TEOW), hosted at
storage.googleapis.com/teow2016/Ecoregions2017.zip. ~847 ecoregions keyed
by the canonical integer `ECO_ID` (e.g. `1` = Admiralty Islands lowland
rain forests, `2` = Aegean and Western Turkey sclerophyllous and mixed forests).
Name comes from `ECO_NAME`.

Note: the `teow` prefix is not (yet) in the backend's `Gazetteer.java` enum.
It is shipped here so the geometries are available when CoL data starts
referencing TEOW ecoregions; the backend needs a matching enum entry before
those lookups resolve.
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

PREFIX = "teow"
SOURCE_URL = "https://storage.googleapis.com/teow2016/Ecoregions2017.zip"
SOURCE_NAME = "RESOLVE Ecoregions 2017 (Dinerstein et al.)"
SOURCE_FILENAME = "Ecoregions2017.zip"
UPSTREAM_VERSION = "2017"

ID_FIELD = "ECO_ID"
NAME_FIELD = "ECO_NAME"


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

    vsizip_src = f"/vsizip/{zip_path}/Ecoregions2017.shp"
    fc_path = work_dir / "all.geojson"
    to_geojson(vsizip_src, fc_path, config)
    print(f"[{PREFIX}] ogr2ogr → {fc_path}")

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
