"""Build the `costello` gazetteer — the 30 marine biogeographic realms of
Costello et al. 2017.

Source: the authors' shapefile on figshare (University of Auckland, article
5596840, CC BY 4.0), `MarineRealmsShapeFile.zip` → `MarineRealms.shp`. 30
polygons covering the whole ocean, keyed by the integer `Realm` (1–30), which
is also our id. The realms were delineated by clustering ~65,000 marine species
(OBIS) on 5° cells by Jaccard similarity: Costello MJ, Tsai P, Wong PS, Cheung
AKL, Basher Z, Chaudhary C (2017) Marine biogeographic realms and species
endemicity. Nature Communications 8: 1057.

The shapefile carries only the number. Names come from the paper's Fig. 1,
finest column of the classification hierarchy, with the compass abbreviations
spelled out (`NE Atlantic` → `North-east Atlantic`). Realm 8's full name
("N Atlantic boreal & sub-Arctic from Canada to Greenland Sea") is shortened.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config
from common.download import download
from common.ids import assert_unique
from common.labels import write_labels
from common.manifest import write_manifest
from common.ogr import split_features, to_geojson

PREFIX = "costello"
SOURCE_URL = "https://ndownloader.figshare.com/files/9737926"
SOURCE_NAME = "Marine biogeographic realms (Costello et al. 2017), figshare 5596840"
SOURCE_FILENAME = "MarineRealmsShapeFile.zip"
SOURCE_DIR = SOURCES_DIR / PREFIX
UPSTREAM_VERSION = "2017"
SHAPEFILE = "MarineRealms.shp"

ID_FIELD = "Realm"
NAME_FIELD = "name"

# Costello et al. 2017, Fig. 1.
REALM_NAMES = {
    1: "Inner Baltic Sea",
    2: "Black Sea",
    3: "North-east Atlantic",
    4: "Arctic Europe",
    5: "Mediterranean",
    6: "Arctic",
    7: "North Pacific",
    8: "North Atlantic boreal & sub-Arctic",
    9: "Mid-tropical North Pacific Ocean",
    10: "South-east Pacific",
    11: "Tropical West Atlantic",
    12: "Tropical East Pacific",
    13: "Tropical Indo-Pacific & coastal Indian Ocean",
    14: "Red Sea",
    15: "Tasman Sea to South-west Pacific",
    16: "Tropical Australia & Coral Sea",
    17: "Mid South Tropical Pacific",
    18: "Offshore & North-west North Atlantic",
    19: "Offshore Indian Ocean",
    20: "Offshore West Pacific",
    21: "Offshore South Atlantic",
    22: "Offshore mid-East Pacific",
    23: "Tropical East Atlantic",
    24: "Argentina",
    25: "Chile",
    26: "South Australia",
    27: "South Africa",
    28: "New Zealand",
    29: "North West Pacific",
    30: "Southern Ocean",
}


def download_source(force: bool):
    """Fetch (or reuse) the figshare zip."""
    return download(
        SOURCE_URL,
        SOURCE_DIR,
        role="shapefile",
        name=SOURCE_NAME,
        filename=SOURCE_FILENAME,
        force=force,
        upstream_version=UPSTREAM_VERSION,
    )


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

    zip_path, source_record = download_source(args.force)
    print(f"[{PREFIX}] source: {zip_path} ({source_record.size_bytes:,} bytes, md5={source_record.md5[:8]}…)")

    # /vsizip/ + absolute archive path needs the double slash preserved, so
    # build it as a plain string — not a pathlib.Path.
    fc_path = work_dir / "all.geojson"
    to_geojson(f"/vsizip/{zip_path}/{SHAPEFILE}", fc_path, config)

    with fc_path.open("r", encoding="utf-8") as f:
        fc = json.load(f)
    seen: set[int] = set()
    for feat in fc["features"]:
        realm = int(feat["properties"][ID_FIELD])
        if realm not in REALM_NAMES:
            raise ValueError(f"unknown Costello realm number {realm}")
        seen.add(realm)
        feat["properties"][NAME_FIELD] = REALM_NAMES[realm]
    missing = sorted(set(REALM_NAMES) - seen)
    if missing:
        raise ValueError(f"Costello realms missing from source: {missing}")
    with fc_path.open("w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)

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
