"""Build the `gi` gazetteer — Global Islands (USGS / Esri / UNEP-WCMC).

Source: the Global Islands file geodatabase, a 30 m resolution island polygon
layer derived from a Global Shoreline Vector built from 2014 Landsat composites.
Published by USGS in partnership with Esri and UNEP-WCMC; USGS/Esri contributed
the shoreline geometry, UNEP-WCMC an earlier island layer that was conflated in.

Scope: the **BigIslands** layer (>1 km2) only, restricted to islands carrying a
USGS name. Two deliberate exclusions, both the result of measuring the source:

  * **Size.** Islands under 1 km2 are below the granularity a regional checklist
    works at, and the source's small classes are dominated by generic names —
    23 distinct islands called "Round Island", 10 called "Redonda". Upstream
    also ships `Mainlands` (continents), which has neither a name field nor
    `ALL_Uniq`, so it could not be keyed even if it were in scope.
  * **Name source.** Only `Name_USGSO` is trusted. It was compiled per island by
    USGS against Google Earth. `NAME_wcmcI` / `NAME_LOCAL` came from UNEP-WCMC
    via a nearest-neighbour spatial join (the `NEAR_FID` / `NEAR_DIST` /
    `Meaning_AL` columns are its residue), and that join leaked badly: taking any
    non-blank name yields 2,768 features labelled "Baffin Island", 805
    "Iceland", 657 "Cuba" — islets inheriting the name of the large island
    beside them — and only 31 % of features end up uniquely named. The
    dataset's own join-quality flag does not separate the good from the bad
    (`Meaning_AL` marks only 43 % of the known-bad cases as non-intersecting,
    because the coarse WCMC polygons genuinely overlap their neighbouring
    islets). Restricting to `Name_USGSO` raises unique naming to 78 %.

`Name_USGSO` also uses the literal string `UNNAMED` as a placeholder, which a
plain "is not empty" test does not catch — 6,659 big islands carry it.

Ids are `ALL_Uniq`, documented upstream as the identifier that is unique across
all four size classes (unlike `USGS_ISID`, which is per-polygon).

Note: the source is unzipped into `sources/gi/` instead of being read through
`/vsizip/`. OpenFileGDB does random seeks across the `.gdbtable` files, and
through a deflated zip member that means repeated decompression.

The backend's `Gazetteer.GI` enum entry is deployed (pattern `^[0-9]+$`), so
`gi:{ALL_Uniq}` references resolve to both a label and a GeoJSON geometry.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config  # noqa: E402
from common.download import download  # noqa: E402
from common.ids import assert_unique  # noqa: E402
from common.labels import write_labels  # noqa: E402
from common.manifest import write_manifest  # noqa: E402
from common.ogr import split_features, to_geojson  # noqa: E402

PREFIX = "gi"
ITEM_ID = "885a860af66d4833887dcce735a521a7"
SOURCE_URL = f"https://www.arcgis.com/sharing/rest/content/items/{ITEM_ID}/data"
SOURCE_PAGE = f"https://www.arcgis.com/home/item.html?id={ITEM_ID}"
SOURCE_NAME = "Global Islands file geodatabase (USGS/Esri/UNEP-WCMC)"
SOURCE_FILENAME = "GlbIslands.gdb.zip"
UPSTREAM_VERSION = "3"
GDB_NAME = "GlbIslands.gdb"

LAYER = "BigIslands"
ID_FIELD = "ALL_Uniq"
NAME_FIELD = "Name_USGSO"
AREA_FIELD = "Area_Geode"
PLATE_FIELD = "Plate"
KEEP_FIELDS = (ID_FIELD, NAME_FIELD, AREA_FIELD, PLATE_FIELD)

# Big islands are complex enough that the repo-wide 0.005° default flattens
# 6.4 % of them to a triangle or less; 0.002° brings that to 0.1 % and still
# halves the tree against 0.001°. Degrees for 4326, metres for 3857.
DEFAULT_SIMPLIFY = {"4326": 0.002, "3857": 220.0}

# Guards against a silent upstream change or a silent -skipfailures drop.
EXPECTED_NAMED = 15139
EXPECTED_UNNAMED = 7332

# `Name_USGSO` is blank as an empty string, as a *single space*, or as the
# literal placeholder `UNNAMED`. TRIM() is unavailable: OGRSQL has no such
# function and the SQLite dialect is not compiled into every GDAL build.
BLANK_NAMES = ("", " ", "UNNAMED")
NAMED_SQL = f"{NAME_FIELD} IS NOT NULL AND " + " AND ".join(
    f"{NAME_FIELD} <> '{v}'" for v in BLANK_NAMES
)
UNNAMED_SQL = f"NOT ({NAMED_SQL})"


def _clean(value: object) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text in BLANK_NAMES else text


def unpack(zip_path: Path, dest_dir: Path, *, force: bool) -> Path:
    """Unzip the geodatabase into `dest_dir`, returning the `.gdb` directory."""
    gdb = dest_dir / GDB_NAME
    if gdb.is_dir() and not force:
        return gdb
    if gdb.is_dir():
        shutil.rmtree(gdb)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)
    if gdb.is_dir():
        return gdb
    candidates = sorted(p for p in dest_dir.glob("**/*.gdb") if p.is_dir())
    if not candidates:
        raise FileNotFoundError(f"no .gdb directory inside {zip_path}")
    return candidates[0]


def query_rows(gdb: Path, sql: str, fields: list[str]) -> list[dict[str, str]]:
    """Run an attribute-only OGRSQL query and parse ogrinfo's output."""
    out = subprocess.run(
        ["ogrinfo", "-q", "-dialect", "OGRSQL", "-sql", sql, str(gdb)],
        check=True, capture_output=True, text=True,
    ).stdout
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("OGRFeature"):
            if current:
                rows.append(current)
            current = {}
            continue
        for field in fields:
            prefix = f"{field} ("
            if line.startswith(prefix) and "=" in line:
                current[field] = line.split("=", 1)[1].strip()
                break
    if current:
        rows.append(current)
    return rows


def prepare(gdb: Path, work_path: Path, config) -> int:
    """ogr2ogr the named big islands, then tidy their properties in place.

    Returns the feature count. `split_features` writes every property into every
    feature file, so the redundant columns are dropped here rather than shipped
    15k times over.
    """
    to_geojson(
        str(gdb), work_path, config,
        layer=LAYER, where=NAMED_SQL, select=KEEP_FIELDS,
    )
    with work_path.open("r", encoding="utf-8") as f:
        fc = json.load(f)

    for feat in fc["features"]:
        props = feat.get("properties") or {}
        name = _clean(props.get(NAME_FIELD))
        if not name:
            raise ValueError(
                f"{props.get(ID_FIELD)}: passed the named filter but resolves to "
                f"an empty name — NAMED_SQL and _clean() disagree"
            )
        kept = {ID_FIELD: props.get(ID_FIELD), "name": name}
        if props.get(AREA_FIELD) is not None:
            kept[AREA_FIELD] = props[AREA_FIELD]
        plate = _clean(props.get(PLATE_FIELD))
        if plate:
            kept[PLATE_FIELD] = plate
        feat["properties"] = kept

    with work_path.open("w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)
    return len(fc["features"])


def report_unnamed(gdb: Path, out_path: Path) -> int:
    """Write the big islands we had to skip for lack of a USGS name.

    These are the interesting gap, and there are far more of them than an empty
    name field suggests: most carry the literal placeholder `UNNAMED`. The
    largest is bigger than Cyprus, and a whole contiguous `ALL_Uniq` block of
    them is unattributed Antarctic islands.
    """
    fields = [ID_FIELD, "USGS_ISID", AREA_FIELD, PLATE_FIELD, NAME_FIELD]
    rows = query_rows(
        gdb,
        f"SELECT {', '.join(fields)} FROM {LAYER} WHERE {UNNAMED_SQL} "
        f"ORDER BY {AREA_FIELD} DESC",
        fields,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(fields)
        for row in rows:
            writer.writerow([row.get(field, "") for field in fields])

    placeholder = sum(1 for r in rows if r.get(NAME_FIELD, "").strip() == "UNNAMED")
    print(
        f"[{PREFIX}] {len(rows)} big islands skipped for lack of a USGS name "
        f"({placeholder} marked literally UNNAMED) → {out_path}"
    )
    for row in rows[:10]:
        area = row.get(AREA_FIELD, "?")
        try:
            area = f"{float(area):,.0f} km2"
        except ValueError:
            pass
        print(f"[{PREFIX}]   {ID_FIELD}={row.get(ID_FIELD, '?'):>8}  {area}")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crs", choices=["4326", "3857"], default=None)
    parser.add_argument(
        "--simplify", type=float, default=None,
        help=f"override the per-gazetteer default ({DEFAULT_SIMPLIFY})",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    # CLI > env > this gazetteer's default > the repo-wide default.
    crs = args.crs or os.environ.get("GAZETTEER_CRS", "4326")
    env_simplify = os.environ.get("GAZETTEER_SIMPLIFY")
    simplify = (
        args.simplify
        if args.simplify is not None
        else float(env_simplify) if env_simplify
        else DEFAULT_SIMPLIFY[crs]
    )
    config = load_config(cli_crs=args.crs, cli_simplify=simplify)

    out_dir = REPO_ROOT / PREFIX
    features_dir = out_dir / "features"
    work_dir = WORK_DIR / PREFIX
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{PREFIX}] target CRS: {config.epsg}, simplify {config.simplify_tolerance}")

    zip_path, source_record = download(
        SOURCE_URL,
        SOURCES_DIR / PREFIX,
        role="filegdb",
        name=SOURCE_NAME,
        filename=SOURCE_FILENAME,
        force=args.force,
        upstream_version=UPSTREAM_VERSION,
        timeout=600,
    )
    print(f"[{PREFIX}] source: {zip_path} ({source_record.size_bytes:,} bytes, md5={source_record.md5[:8]}…)")

    gdb = unpack(zip_path, SOURCES_DIR / PREFIX, force=args.force)
    print(f"[{PREFIX}] geodatabase: {gdb}")

    work_path = work_dir / "bigislands.geojson"
    count = prepare(gdb, work_path, config)
    if count != EXPECTED_NAMED:
        print(
            f"[{PREFIX}] WARNING {count} named big islands, expected "
            f"{EXPECTED_NAMED} — upstream may have changed"
        )

    rows = split_features(
        work_path, features_dir, id_field=ID_FIELD, name_field="name",
    )
    assert_unique([r[0] for r in rows])
    label_count = write_labels(out_dir / "labels.tsv", rows)
    feature_count = len(list(features_dir.glob("*.geojson")))
    print(f"[{PREFIX}] split → {feature_count} features, {label_count} labels")
    if feature_count != count:
        raise ValueError(
            f"wrote {feature_count} features but read {count} named islands — "
            f"ogr2ogr -skipfailures or a degenerate geometry dropped "
            f"{count - feature_count}"
        )

    skipped = report_unnamed(gdb, work_dir / "unnamed-big-islands.tsv")
    if skipped != EXPECTED_UNNAMED:
        print(
            f"[{PREFIX}] WARNING {skipped} unnamed big islands, expected "
            f"{EXPECTED_UNNAMED} — upstream may have changed"
        )

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
