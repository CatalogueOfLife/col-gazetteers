"""Build the `mrgid` gazetteer from a curated union of VLIZ WFS layers.

The MarineRegions gazetteer is huge; the `MarineRegions:gazetteer_polygon`
WFS layer only exposes ~48 misc features. The bulk of MRGIDs CoL data
references live in themed layers (EEZ, LME, IHO, FAO, Longhurst, …). We
fetch each, key on `mrgid`, and dedupe — a feature wins from the first
layer it appears in (LAYER order is most-specific → most-generic).

Same VLIZ GeoServer used for `iho`. No form-gated downloads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config
from common.download import download
from common.labels import write_labels
from common.manifest import write_manifest
from common.ogr import split_features, to_geojson

PREFIX = "mrgid"
WFS_BASE = (
    "https://geo.vliz.be/geoserver/MarineRegions/ows"
    "?service=WFS&version=2.0.0&request=GetFeature&outputFormat=application/json"
    "&typeNames=MarineRegions:"
)

# (layer, role, name_field). Order matters: a feature appearing in multiple
# layers keeps the first match. EEZ first since it's the most-cited in
# distribution data. `goas` is intentionally excluded — its WFS schema
# carries no MRGID. ICES layers use non-standard name fields.
LAYERS = [
    ("eez", "eez", "geoname"),
    ("lme", "large-marine-ecosystem", "lme_name"),
    ("iho", "iho-sea-area", "name"),
    ("fao", "fao-fishing-area", "name"),
    ("longhurst", "longhurst-province", "provdescr"),
    ("high_seas", "high-seas", "name"),
    ("ecs", "extended-continental-shelf", "geoname"),
    ("ices_areas", "ices-area", "ices_area"),
    ("ices_ecoregions", "ices-ecoregion", "ecoregion"),
    ("arcticmarineareas", "arctic-marine-area", "name"),
    ("gazetteer_polygon", "gazetteer-misc", "name"),
]

ID_FIELD = "mrgid"


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
    features_dir.mkdir(parents=True, exist_ok=True)
    for old in features_dir.glob("*.geojson"):
        old.unlink()

    print(f"[{PREFIX}] target CRS: {config.epsg}, simplify {config.simplify_tolerance}")

    sources = []
    all_rows: list[tuple[str, str]] = []
    for layer, role, name_field in LAYERS:
        url = f"{WFS_BASE}{layer}"
        filename = f"{layer}.geojson"
        src_path, record = download(
            url,
            SOURCES_DIR / PREFIX,
            role=role,
            name=f"MarineRegions:{layer} via WFS",
            filename=filename,
            force=args.force,
        )
        sources.append(record)

        work_fc = work_dir / filename
        to_geojson(src_path, work_fc, config)
        rows = split_features(
            work_fc, features_dir,
            id_field=ID_FIELD, name_field=name_field,
            clear=False,
            source_tag=role,
        )
        all_rows.extend(rows)
        print(f"[{PREFIX}] {layer}: {len(rows)} new features ({record.size_bytes:,} bytes source)")

    # Dedupe by id (split_features already merges geometry on collision; we
    # just need the (id, name) pairs collapsed for labels.tsv).
    seen: dict[str, str] = {}
    for area_id, name in all_rows:
        seen.setdefault(area_id, name)
    rows_unique = list(seen.items())
    label_count = write_labels(out_dir / "labels.tsv", rows_unique)
    feature_count = len(list(features_dir.glob("*.geojson")))
    print(f"[{PREFIX}] total: {feature_count} unique features, {label_count} labels")

    write_manifest(
        out_dir / "build.json",
        prefix=PREFIX,
        config=config,
        sources=sources,
        feature_count=feature_count,
        label_count=label_count,
    )
    print(f"[{PREFIX}] manifest → {out_dir / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
