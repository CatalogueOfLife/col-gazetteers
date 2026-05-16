"""Build the `iso` gazetteer — ISO 3166-1 alpha-2 countries + ISO 3166-2 subdivisions.

Source: Natural Earth 10m cultural shapefiles (admin_0_countries +
admin_1_states_provinces), the standard public-domain country / subdivision
geometry source.

Both kinds share one output namespace under `iso/features/`:
- 2-letter country codes:  `US.geojson`, `DE.geojson`, …
- 4–6-char subdivision codes: `US-CA.geojson`, `DE-BY.geojson`, …

All ids are stored upper-case (per ISO 3166). Features without a valid ISO
code (Natural Earth uses `-99` for non-ISO territories; subdivisions in
disputed / unrecognised areas often have an empty `iso_3166_2`) are skipped.

The backend's bundled vocabularies only cover ISO 3166-1 country codes — this
repo is the authoritative source for the 3166-2 subdivision labels.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config
from common.download import download
from common.ids import assert_unique
from common.labels import write_labels
from common.manifest import write_manifest
from common.ogr import split_features, to_geojson

PREFIX = "iso"
NE_BASE = "https://naciscdn.org/naturalearth/10m/cultural"

ID_FIELD = "iso_id"  # synthetic, written by the pre-processing pass below
NAME_FIELD = "iso_name"

# Subdivision codes from Natural Earth must match this pattern; anything
# else (empty string, partial code like "US-") is silently dropped.
_ISO_3166_2_RE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,3}$")
_ISO_3166_1_RE = re.compile(r"^[A-Z]{2}$")


def _preprocess_countries(fc: dict) -> int:
    """Add iso_id (alpha-2) + iso_name (English) to each country feature; drop
    those without a valid ISO 3166-1 code. Returns the kept feature count."""
    kept = []
    for feat in fc["features"]:
        props = feat.get("properties") or {}
        raw = (props.get("ISO_A2") or props.get("ISO_A2_EH") or "").strip().upper()
        if not _ISO_3166_1_RE.match(raw) or raw == "-9":
            continue
        name = (props.get("NAME_EN")
                or props.get("NAME_LONG")
                or props.get("ADMIN")
                or props.get("NAME")
                or "").strip()
        props["iso_id"] = raw
        props["iso_name"] = name
        feat["properties"] = props
        kept.append(feat)
    fc["features"] = kept
    return len(kept)


def _preprocess_subdivisions(fc: dict) -> int:
    """Add iso_id (CC-XX) + iso_name to each subdivision feature; drop those
    without a parseable ISO 3166-2 code."""
    kept = []
    for feat in fc["features"]:
        props = feat.get("properties") or {}
        raw = (props.get("iso_3166_2") or "").strip().upper()
        if not _ISO_3166_2_RE.match(raw):
            continue
        name = (props.get("name_en")
                or props.get("name")
                or props.get("gn_name")
                or "").strip()
        props["iso_id"] = raw
        props["iso_name"] = name
        feat["properties"] = props
        kept.append(feat)
    fc["features"] = kept
    return len(kept)


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

    for name, role, level_preprocess in [
        ("ne_10m_admin_0_countries",        "iso-3166-1-countries",    _preprocess_countries),
        ("ne_10m_admin_1_states_provinces", "iso-3166-2-subdivisions", _preprocess_subdivisions),
    ]:
        url = f"{NE_BASE}/{name}.zip"
        zip_path, record = download(
            url, SOURCES_DIR / PREFIX,
            role=role, name=f"Natural Earth 10m: {name}",
            filename=f"{name}.zip", force=args.force,
            upstream_version="10m",
        )
        sources.append(record)
        print(f"[{PREFIX}] {name}: {record.size_bytes:,} bytes md5={record.md5[:8]}…")

        vsizip_src = f"/vsizip/{zip_path}/{name}.shp"
        raw_fc = work_dir / f"{name}.geojson"
        to_geojson(vsizip_src, raw_fc, config)

        with raw_fc.open("r", encoding="utf-8") as f:
            fc = json.load(f)
        before = len(fc["features"])
        kept = level_preprocess(fc)
        rewritten_fc = work_dir / f"{name}.iso.geojson"
        with rewritten_fc.open("w", encoding="utf-8") as f:
            json.dump(fc, f, ensure_ascii=False)
        print(f"[{PREFIX}] {name}: kept {kept}/{before} features with valid ISO codes")

        rows = split_features(
            rewritten_fc, features_dir,
            id_field=ID_FIELD, name_field=NAME_FIELD,
            clear=False,
            source_tag=role,
        )
        all_rows.extend(rows)

    assert_unique([r[0] for r in all_rows])
    label_count = write_labels(out_dir / "labels.tsv", all_rows)
    feature_count = len(list(features_dir.glob("*.geojson")))
    print(f"[{PREFIX}] total: {feature_count} features, {label_count} labels")

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
