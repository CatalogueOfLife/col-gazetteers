"""Build the `mrgid` gazetteer for *every* MarineRegions MRGID.

Strategy:

  1. Enumerate the full gazetteer by walking every placeType via
     `getGazetteerRecordsByType` (paginated, 100 records per page).
  2. For each MRGID, resolve its (layer, attribute, value) geometry pointers
     via `getGazetteerWMSes`. A single MRGID may have zero pointers (point-
     only entries), one, or many (e.g. an IHO sea split across several
     polygons in the layer).
  3. Group pointers by (namespace, featureType) and bulk-fetch each layer
     once via WFS. The current themed layers (eez, iho, fao, lme, longhurst,
     high_seas, ecs, ices_*, arcticmarineareas, gazetteer_polygon) appear
     here, plus any others MarineRegions points us at — most notably
     `World:world_quadrants_20150805` for "General Region"-typed entries.
  4. Slice each layer by its referenced (attribute, value) pairs. Features
     are written per MRGID; multiple matches for one MRGID merge into a
     MultiPolygon.
  5. For MRGIDs with no WMS pointer at all, fall back to a Point GeoJSON
     built from the record's centroid so the ID still resolves.

Both REST responses and WFS source files are cached on disk (`work/mrgid/`
and `sources/mrgid/`). Reruns only re-fetch missing entries. `--force` wipes
the caches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config
from common.download import download
from common.ids import normalize_id
from common.labels import write_labels
from common.manifest import write_manifest
from common.mrgid_api import (
    GazetteerRecord,
    WMSPointer,
    build_cql_filter,
    fetch_records_by_type,
    fetch_wms_pointers_parallel,
    get_place_types,
    wfs_geojson_url,
)
from common.ogr import _merge_geometries, to_geojson

PREFIX = "mrgid"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crs", choices=["4326", "3857"], default=None)
    parser.add_argument(
        "--force", action="store_true",
        help="wipe REST and WFS caches and re-fetch everything",
    )
    parser.add_argument(
        "--workers", type=int, default=16,
        help="parallel HTTP workers for WMS pointer lookup (default 16)",
    )
    parser.add_argument(
        "--limit-types", type=int, default=None,
        help="dev: only enumerate the first N placeTypes (alphabetical)",
    )
    parser.add_argument(
        "--type", action="append", default=None,
        help="dev: only enumerate the named placeType(s) (repeatable); "
             "exact match against getGazetteerTypes",
    )
    args = parser.parse_args()

    config = load_config(cli_crs=args.crs)
    out_dir = REPO_ROOT / PREFIX
    features_dir = out_dir / "features"
    sources_dir = SOURCES_DIR / PREFIX
    work_dir = WORK_DIR / PREFIX
    cache_dir = work_dir / "api"
    wfs_dir = work_dir / "wfs"

    if args.force:
        for d in (cache_dir, sources_dir, wfs_dir):
            if d.exists():
                shutil.rmtree(d)
    for d in (sources_dir, work_dir, cache_dir, wfs_dir, features_dir):
        d.mkdir(parents=True, exist_ok=True)
    for old in features_dir.glob("*.geojson"):
        old.unlink()

    print(f"[{PREFIX}] target CRS: {config.epsg}, simplify {config.simplify_tolerance}")

    # ---- Phase 1: enumerate records by placeType ----
    types = sorted(get_place_types(cache_dir))
    if args.type:
        wanted = set(args.type)
        missing = wanted - set(types)
        if missing:
            raise SystemExit(f"unknown placeType(s): {sorted(missing)}")
        types = sorted(wanted)
        print(f"[{PREFIX}] LIMIT: only {len(types)} explicit type(s) (dev mode)")
    elif args.limit_types:
        types = types[: args.limit_types]
        print(f"[{PREFIX}] LIMIT: only {len(types)} types (dev mode)")
    print(f"[{PREFIX}] enumerating {len(types)} placeTypes…")
    records: dict[int, GazetteerRecord] = {}
    for i, type_name in enumerate(types, 1):
        recs = fetch_records_by_type(type_name, cache_dir)
        # First placeType wins for a given MRGID. Alphabetical order makes
        # the choice deterministic across runs; the canonical placeType is
        # already in record.place_type so the tie-breaking only affects
        # which centroid+source pair we keep.
        for r in recs:
            records.setdefault(r.mrgid, r)
        print(f"  [{i:3d}/{len(types)}] {type_name}: {len(recs)} recs "
              f"({len(records)} unique total)")
    print(f"[{PREFIX}] enumerated {len(records)} unique MRGIDs")

    # ---- Phase 2: resolve WMS pointers ----
    print(f"[{PREFIX}] resolving WMS pointers (workers={args.workers})…")

    def _progress(done: int, total: int) -> None:
        print(f"  {done}/{total} pointers resolved")

    pointers = fetch_wms_pointers_parallel(
        list(records.keys()), cache_dir,
        workers=args.workers, on_progress=_progress,
    )
    mrgids_with_pointer = sum(1 for ps in pointers.values() if ps)
    print(f"[{PREFIX}] {mrgids_with_pointer} MRGIDs have ≥1 WMS pointer, "
          f"{len(records) - mrgids_with_pointer} have none")

    # Group pointers by (namespace, featureType) layer.
    by_layer: dict[tuple[str, str], list[WMSPointer]] = defaultdict(list)
    for ps in pointers.values():
        for p in ps:
            by_layer[(p.namespace, p.feature_type)].append(p)
    print(f"[{PREFIX}] {len(by_layer)} unique WFS layers referenced:")
    for (ns, ft), ps in sorted(by_layer.items()):
        print(f"    {ns}:{ft}  ({len(ps)} pointer refs)")

    # ---- Phase 3: bulk-fetch each WFS layer once ----
    # Build a CQL filter per layer from the union of pointer (attr, value)
    # pairs so the server only returns rows we'll use. Some layers (e.g.
    # World:worldgazetteer @ 150k features) are unusable without this.
    # Fall back to unfiltered when the URL would exceed a safe length budget.
    URL_BUDGET = 6000  # GeoServer typically tolerates up to ~8k
    sources = []
    layer_features: dict[tuple[str, str], list[dict]] = {}
    failed_layers: list[tuple[str, str, str]] = []
    for (ns, ft), ptrs in sorted(by_layer.items()):
        cql = build_cql_filter(ptrs)
        candidate = wfs_geojson_url(ns, ft, cql_filter=cql) if cql else None
        if cql and candidate and len(candidate) <= URL_BUDGET:
            url = candidate
            fp = hashlib.sha256(cql.encode()).hexdigest()[:8]
            cql_tag = "filtered"
        else:
            url = wfs_geojson_url(ns, ft)
            fp = "all"
            cql_tag = "unfiltered"
        # Cache key incorporates the filter so a re-run with a different
        # pointer set re-downloads instead of reusing a stale slice.
        filename = f"{ns}__{ft}__{fp}.geojson"
        try:
            src_path, record = download(
                url, sources_dir,
                role=f"wfs:{ns}:{ft}",
                name=f"{ns}:{ft} via WFS ({cql_tag})",
                filename=filename,
                force=False,
            )
        except Exception as e:
            # A layer the gazetteer points at may be retired or temporarily
            # unavailable. Skip it — the MRGIDs that depended on it will
            # drop to the centroid-Point fallback.
            print(f"[{PREFIX}] SKIP {ns}:{ft}: {e}")
            failed_layers.append((ns, ft, str(e)))
            continue
        sources.append(record)
        work_fc = wfs_dir / filename
        try:
            if not work_fc.exists():
                to_geojson(src_path, work_fc, config)
            with work_fc.open("r", encoding="utf-8") as f:
                fc = json.load(f)
        except Exception as e:
            print(f"[{PREFIX}] SKIP {ns}:{ft} (ogr2ogr/parse): {e}")
            failed_layers.append((ns, ft, str(e)))
            continue
        feats = fc.get("features") or []
        layer_features[(ns, ft)] = feats
        print(f"[{PREFIX}] {ns}:{ft}: {len(feats)} features in layer "
              f"({record.size_bytes:,} bytes source)")

    # ---- Phase 4: slice layers by pointer + write per-MRGID features ----
    # slice_index[(ns, ft, attribute)][value] = [mrgid, ...]
    slice_index: dict[tuple[str, str, str], dict[str, list[int]]] = \
        defaultdict(lambda: defaultdict(list))
    for mrgid, ps in pointers.items():
        for p in ps:
            slice_index[(p.namespace, p.feature_type, p.attribute)][p.value].append(mrgid)

    polygon_mrgids: set[int] = set()
    for (ns, ft), feats in layer_features.items():
        # Attributes referenced by any pointer for this layer
        attrs = {a for (ns2, ft2, a) in slice_index if (ns2, ft2) == (ns, ft)}
        if not attrs:
            continue
        for feat in feats:
            props = feat.get("properties") or {}
            for attr in attrs:
                raw = props.get(attr)
                if raw is None:
                    continue
                mrgids = _lookup(slice_index[(ns, ft, attr)], raw)
                if not mrgids:
                    continue
                for mrgid in mrgids:
                    _write_or_merge_feature(
                        features_dir, mrgid, records.get(mrgid),
                        feat, source_tag=f"{ns}:{ft}",
                    )
                    polygon_mrgids.add(mrgid)
                break  # one attribute match per feature is enough

    # ---- Phase 5: centroid-Point fallback ----
    centroid_mrgids: set[int] = set()
    bbox_centroid_mrgids: set[int] = set()
    skipped_no_geom = 0
    for mrgid, rec in records.items():
        if mrgid in polygon_mrgids:
            continue
        if rec.latitude is not None and rec.longitude is not None:
            _write_point_feature(features_dir, mrgid, rec,
                                 rec.longitude, rec.latitude, "centroid", config)
            centroid_mrgids.add(mrgid)
            continue
        # No point centroid, but maybe a bounding box (typical for "General
        # Region" records that span a wide area without one canonical point).
        if (rec.min_lat is not None and rec.max_lat is not None
                and rec.min_lon is not None and rec.max_lon is not None):
            lon = (rec.min_lon + rec.max_lon) / 2
            lat = (rec.min_lat + rec.max_lat) / 2
            _write_point_feature(features_dir, mrgid, rec, lon, lat,
                                 "bbox-centroid", config)
            bbox_centroid_mrgids.add(mrgid)
            continue
        skipped_no_geom += 1
    centroid_count = len(centroid_mrgids) + len(bbox_centroid_mrgids)

    # ---- labels.tsv: only MRGIDs that ended up with a feature file ----
    # Records with neither a polygon nor a centroid (lat/lon None — typically
    # tombstoned or coordinate-less upstream entries) are dropped from
    # labels.tsv too, so the resolver contract `id ∈ labels.tsv ⇔ feature file
    # exists` holds. test_id_patterns.py enforces this.
    written_mrgids = polygon_mrgids | centroid_mrgids | bbox_centroid_mrgids
    rows = [
        (str(rec.mrgid), rec.name, rec.place_type)
        for rec in records.values()
        if rec.mrgid in written_mrgids
    ]
    label_count = write_labels(out_dir / "labels.tsv", rows)
    feature_count = len(list(features_dir.glob("*.geojson")))
    print(f"[{PREFIX}] total: {feature_count} features "
          f"({len(polygon_mrgids)} polygon + {len(centroid_mrgids)} point "
          f"+ {len(bbox_centroid_mrgids)} bbox-centroid), "
          f"{label_count} labels, {skipped_no_geom} skipped (no geometry at all)")
    if failed_layers:
        print(f"[{PREFIX}] {len(failed_layers)} layer(s) failed:")
        for ns, ft, err in failed_layers:
            print(f"    {ns}:{ft}  → {err[:120]}")

    write_manifest(
        out_dir / "build.json",
        prefix=PREFIX, config=config, sources=sources,
        feature_count=feature_count, label_count=label_count,
    )
    print(f"[{PREFIX}] manifest → {out_dir / 'build.json'}")
    return 0


def _lookup(value_to_mrgids: dict[str, list[int]], raw) -> list[int]:
    """Match a WFS attribute value against the pointer-value index.

    Pointer values are always strings. WFS properties may be int/float/str.
    Try the obvious normalizations: as-is, int-stringified (for "18000.0").
    """
    # Direct
    s = str(raw)
    if s in value_to_mrgids:
        return value_to_mrgids[s]
    # Numeric coerce
    if isinstance(raw, float) and raw.is_integer():
        s2 = str(int(raw))
        if s2 in value_to_mrgids:
            return value_to_mrgids[s2]
    if isinstance(raw, int):
        s2 = str(raw)
        if s2 in value_to_mrgids:
            return value_to_mrgids[s2]
    return []


def _write_or_merge_feature(
    features_dir: Path,
    mrgid: int,
    rec: GazetteerRecord | None,
    incoming: dict,
    *,
    source_tag: str,
) -> None:
    """Write or merge `features/{mrgid}.geojson` keyed by MRGID.

    On collision, geometries are unioned by promotion to a Multi* type via the
    shared `_merge_geometries` helper. The first writer's `source` tag wins.
    """
    path = features_dir / f"{normalize_id(mrgid)}.geojson"
    new_geom = incoming.get("geometry")
    if new_geom is None:
        return
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            existing = json.load(f)
        try:
            existing["geometry"] = _merge_geometries(existing.get("geometry"), new_geom)
        except ValueError:
            # mixed geometry types across pointers — keep the first
            return
        with path.open("w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        return
    feature = {
        "type": "Feature",
        "properties": {
            "mrgid": mrgid,
            "name": rec.name if rec else "",
            "placeType": rec.place_type if rec else "",
            "source": source_tag,
        },
        "geometry": new_geom,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(feature, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def _write_point_feature(
    features_dir: Path,
    mrgid: int,
    rec: GazetteerRecord,
    lon: float,
    lat: float,
    source_tag: str,
    config,
) -> None:
    """Write a Point GeoJSON for an MRGID with no polygon. `source_tag` is
    either 'centroid' (when MR shipped a real lat/lon) or 'bbox-centroid'
    (when only a bounding box was available and we used its center)."""
    # The REST API returns lat/lon in WGS84. If the build target is EPSG:3857
    # we'd need to reproject; for now we only support 4326 here. Bail out
    # explicitly rather than silently writing wrong coordinates.
    if config.crs != "4326":
        raise NotImplementedError(
            "centroid-Point fallback only implemented for EPSG:4326 builds"
        )
    feature = {
        "type": "Feature",
        "properties": {
            "mrgid": mrgid,
            "name": rec.name,
            "placeType": rec.place_type,
            "source": source_tag,
        },
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat],
        },
    }
    path = features_dir / f"{normalize_id(mrgid)}.geojson"
    with path.open("w", encoding="utf-8") as f:
        json.dump(feature, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
