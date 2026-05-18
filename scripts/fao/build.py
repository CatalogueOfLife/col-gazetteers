"""Build the `fao` gazetteer from FAO Fisheries Division GIS.

Source: FAO GeoServer WFS, layer `fifao:FAO_AREAS_NOCOASTLINE` — the master
polygons not erased by coastline. Covers the full CWP hierarchy:

    MAJOR (e.g. 88) → SUBAREA (88.1) → DIVISION (37.4.1) → SUBDIVISION → SUBUNIT

We filter to `F_STATUS='endorsed'` to drop unstable draft breakdowns
(e.g. the two pending Area-31 breakdown options). FAO codes (`F_CODE`) are
used as ids directly, matching how CoL distributions cite them.

Labels carry English (canonical), French and Spanish names — FAO publishes
all three on the same record.
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
# WFS GetFeature, GeoJSON. Server-side filter to endorsed entries that have
# a name — drops e.g. the unnamed `27.3.b, c` placeholder (comma in `F_CODE`,
# all NAME_* blank) which would otherwise produce a normalized id the backend
# regex rejects.
SOURCE_URL = (
    "https://www.fao.org/fishery/geoserver/fifao/ows"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeNames=fifao:FAO_AREAS_NOCOASTLINE"
    "&CQL_FILTER=F_STATUS%3D%27endorsed%27%20AND%20NAME_EN%3C%3E%27%27"
    "&outputFormat=application/json"
)
SOURCE_NAME = "fifao:FAO_AREAS_NOCOASTLINE (FAO Major Fishing Areas, all levels, endorsed)"
SOURCE_FILENAME = "FAO_AREAS_NOCOASTLINE.geojson"

ID_FIELD = "F_CODE"     # hierarchical CWP code, e.g. "88", "88.1", "37.4.1"
NAME_FIELD = "NAME_EN"
EXTRA_FIELDS = ("NAME_FR", "NAME_ES")


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
    # rfc7946=False — Arctic Sea (18) and several NE Atlantic (27.*) features
    # are MultiPolygons whose two parts sit on opposite sides of the
    # antimeridian; the combined bbox spans 360° and trips GDAL's RFC7946
    # writer check even though each polygon is independently valid.
    to_geojson(src_path, fc_path, config, rfc7946=False)

    rows = split_features(
        fc_path, features_dir,
        id_field=ID_FIELD, name_field=NAME_FIELD,
        extra_fields=EXTRA_FIELDS,
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
