"""Build the `tdwg` gazetteer from tdwg/wgsrpd GeoJSON (levels 1–4).

Source: https://github.com/tdwg/wgsrpd — official TDWG WGSRPD repository,
ships one FeatureCollection per level in `geojson/level{1,2,3,4}.geojson`.
All four levels share one output namespace (codes don't collide across levels).

The backend bundles TDWG labels internally; emitting `labels.tsv` here is
optional but useful as a fallback and as documentation of what's covered.
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

PREFIX = "tdwg"
BASE_URL = "https://raw.githubusercontent.com/tdwg/wgsrpd/master/geojson"

# (level, id_field, name_field). Field naming differs per level upstream.
LEVELS = [
    (1, "LEVEL1_COD", "LEVEL1_NAM"),
    (2, "LEVEL2_COD", "LEVEL2_NAM"),
    (3, "LEVEL3_COD", "LEVEL3_NAM"),
    (4, "Level4_cod", "Level_4_Na"),
]
UPSTREAM_VERSION = "master"  # tdwg/wgsrpd doesn't tag releases; pin SHA later if needed


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
    # Clear the features dir once up-front; split_features called per level
    # would otherwise wipe earlier levels' output.
    features_dir.mkdir(parents=True, exist_ok=True)
    for old in features_dir.glob("*.geojson"):
        old.unlink()

    print(f"[{PREFIX}] target CRS: {config.epsg}, simplify {config.simplify_tolerance}")

    sources = []
    all_rows: list[tuple[str, str]] = []
    for level, id_field, name_field in LEVELS:
        filename = f"level{level}.geojson"
        url = f"{BASE_URL}/{filename}"
        src_path, record = download(
            url,
            SOURCES_DIR / PREFIX,
            role=f"geojson-level{level}",
            name=f"tdwg/wgsrpd level{level}",
            filename=filename,
            force=args.force,
            upstream_version=UPSTREAM_VERSION,
        )
        sources.append(record)
        print(f"[{PREFIX}] L{level}: {src_path} ({record.size_bytes} bytes)")

        work_fc = work_dir / f"level{level}.geojson"
        to_geojson(src_path, work_fc, config)
        rows = split_features(
            work_fc, features_dir,
            id_field=id_field, name_field=name_field,
            clear=False,
        )
        all_rows.extend(rows)
        print(f"[{PREFIX}] L{level}: split → {len(rows)} features")

    assert_unique([r[0] for r in all_rows])
    label_count = write_labels(out_dir / "labels.tsv", all_rows)
    print(f"[{PREFIX}] total: {len(all_rows)} features, {label_count} labels")

    write_manifest(
        out_dir / "build.json",
        prefix=PREFIX,
        config=config,
        sources=sources,
        feature_count=len(all_rows),
        label_count=label_count,
    )
    print(f"[{PREFIX}] manifest → {out_dir / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
