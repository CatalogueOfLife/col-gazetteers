"""Build the `iho` gazetteer from VLIZ MarineRegions IHO Sea Areas v3.

Source: VLIZ GeoServer WFS, layer `MarineRegions:iho`. The shapefile zip on
marineregions.org is form-gated, so we use the public WFS endpoint which
returns the same dataset as GeoJSON.

  https://geo.vliz.be/geoserver/MarineRegions/ows?service=WFS&...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `common.*` importable when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config
from common.download import download
from common.ids import assert_unique
from common.labels import write_labels
from common.manifest import write_manifest
from common.ogr import split_features, to_geojson

PREFIX = "iho"
SOURCE_URL = (
    "https://geo.vliz.be/geoserver/MarineRegions/ows"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeNames=MarineRegions:iho&outputFormat=application/json"
)
SOURCE_NAME = "MarineRegions:iho (IHO Sea Areas v3 via WFS)"
SOURCE_FILENAME = "iho.geojson"
UPSTREAM_VERSION = "v3"

# Use the S-23 area number as the id (e.g. North Atlantic Ocean = "23",
# Strait of Gibraltar = "28a", Mediterranean Sea Western Basin = "28A").
# CoL distributions reference IHO areas by S-23 number, not MRGID. The
# MRGID is retained as a property inside each Feature for cross-reference.
ID_FIELD = "id"
NAME_FIELD = "name"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--crs", choices=["4326", "3857"], default=None,
        help="Target CRS (overrides $GAZETTEER_CRS, default 4326).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download source even if cached.",
    )
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
        upstream_version=UPSTREAM_VERSION,
    )
    print(f"[{PREFIX}] source: {src_path} ({source_record.size_bytes} bytes, md5={source_record.md5[:8]}…)")

    fc_path = work_dir / "all.geojson"
    to_geojson(src_path, fc_path, config)
    print(f"[{PREFIX}] ogr2ogr → {fc_path}")

    rows = split_features(
        fc_path, features_dir, id_field=ID_FIELD, name_field=NAME_FIELD,
    )
    assert_unique([r[0] for r in rows])
    label_count = write_labels(out_dir / "labels.tsv", rows)
    print(f"[{PREFIX}] split → {len(rows)} features, {label_count} labels")

    write_manifest(
        out_dir / "build.json",
        prefix=PREFIX,
        config=config,
        sources=[source_record],
        feature_count=len(rows),
        label_count=label_count,
    )
    print(f"[{PREFIX}] manifest → {out_dir / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
