"""Build the `realm` gazetteer — 8 biogeographic realms.

Source: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), the modern update
of Olson et al. 2001 WWF terrestrial ecoregions, hosted at
storage.googleapis.com/teow2016/Ecoregions2017.zip. The shapefile has 847
ecoregions with a `REALM` attribute. We dissolve by REALM into the 8
realms enumerated by the backend's `life.catalogue.api.vocab.area.BioGeoRealm`
(Palearctic, Nearctic, Afrotropic, Neotropic, Australasia, Indomalaya,
Oceania, Antarctic). The source uses two spellings that need rewriting to
match the backend: `Antarctica` → `Antarctic`, `Indomalayan` → `Indomalaya`.
N/A features (rock & ice) are excluded.

Dissolving is done by `split_features`' merge-on-duplicate logic: feeding
the rewritten REALM as id_field, every ecoregion sharing a realm name is
collapsed into one MultiPolygon.
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

PREFIX = "realm"
SOURCE_URL = "https://storage.googleapis.com/teow2016/Ecoregions2017.zip"
SOURCE_NAME = "RESOLVE Ecoregions 2017 (Dinerstein et al.)"
SOURCE_FILENAME = "Ecoregions2017.zip"
UPSTREAM_VERSION = "2017"

# WWF REALM string → backend BioGeoRealm name (the id we write to disk).
REALM_MAP = {
    "Afrotropic":  "Afrotropic",
    "Antarctica":  "Antarctic",
    "Australasia": "Australasia",
    "Indomalayan": "Indomalaya",
    "Nearctic":    "Nearctic",
    "Neotropic":   "Neotropic",
    "Oceania":     "Oceania",
    "Palearctic":  "Palearctic",
}
REALM_FIELD = "realm"  # rewritten field name after our mapping pass


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

    # /vsizip/ + absolute archive path needs the double slash preserved, so
    # build it as a plain string — not a pathlib.Path.
    vsizip_src = f"/vsizip/{zip_path}/Ecoregions2017.shp"
    all_ecoregions = work_dir / "ecoregions.geojson"
    to_geojson(vsizip_src, all_ecoregions, config)
    print(f"[{PREFIX}] ogr2ogr → {all_ecoregions}")

    # Rewrite REALM codes to backend BioGeoRealm names, drop N/A.
    with all_ecoregions.open("r", encoding="utf-8") as f:
        fc = json.load(f)
    kept, skipped = [], 0
    seen_unknown: set[str] = set()
    for feat in fc["features"]:
        wwf = (feat.get("properties") or {}).get("REALM", "")
        name = REALM_MAP.get(wwf)
        if name is None:
            skipped += 1
            if wwf != "N/A":
                seen_unknown.add(wwf)
            continue
        feat["properties"][REALM_FIELD] = name
        kept.append(feat)
    fc["features"] = kept
    by_realm = work_dir / "by_realm.geojson"
    with by_realm.open("w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)
    print(f"[{PREFIX}] kept {len(kept)} ecoregions, skipped {skipped} (N/A or rock&ice)")
    if seen_unknown:
        print(f"[{PREFIX}] WARNING unmapped REALM values: {sorted(seen_unknown)}")

    rows = split_features(
        by_realm, features_dir,
        id_field=REALM_FIELD, name_field=REALM_FIELD,
    )
    assert_unique([r[0] for r in rows])
    label_count = write_labels(out_dir / "labels.tsv", rows)
    feature_count = len(list(features_dir.glob("*.geojson")))
    print(f"[{PREFIX}] dissolved → {feature_count} realms, {label_count} labels")

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
