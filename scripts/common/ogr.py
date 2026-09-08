"""ogr2ogr wrappers — shapefile/zip → simplified GeoJSON FeatureCollection."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .config import Config
from .ids import normalize_id


def _ogr_env() -> dict[str, str]:
    """Env for ogr2ogr subprocess: lift the per-feature GeoJSON size cap so
    large EEZ / MRGID FeatureCollections parse end-to-end."""
    env = os.environ.copy()
    env.setdefault("OGR_GEOJSON_MAX_OBJ_SIZE", "0")
    return env


def to_geojson(
    src: str | Path,
    dest: Path,
    config: Config,
    *,
    layer: str | None = None,
    where: str | None = None,
    select: Sequence[str] | None = None,
    rfc7946: bool = True,
) -> None:
    """Reproject + simplify a shapefile (or any OGR source) into one GeoJSON.

    `src` may be a `.shp` path or `/vsizip//absolute/path/to/foo.zip/inner.shp`.
    Pass vsizip paths as plain strings — `pathlib.Path` would collapse the
    leading `//` that vsizip requires for absolute archive paths.

    Pass `select` to carry only those attribute fields through to the output.
    Worth doing for wide sources with many features: `split_features` writes
    every property into every feature file, so unused upstream columns are paid
    for once per feature.

    Pass `rfc7946=False` to skip GDAL's RFC7946 strict mode for the GeoJSON
    writer. Necessary for sources whose MultiPolygons cross the antimeridian
    (e.g. FAO Arctic Sea / Atlantic NE), where the writer's combined-bbox
    check trips even when each constituent polygon is already on one side.
    Only meaningful when CRS is 4326 (3857 always uses RFC7946=NO).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    src_str = src if isinstance(src, str) else str(src)
    rfc7946_yes = config.crs == "4326" and rfc7946
    cmd = [
        "ogr2ogr",
        "-f", "GeoJSON",
        "-t_srs", config.epsg,
        "-simplify", str(config.simplify_tolerance),
        "-makevalid",          # repair self-intersecting / side-location-conflict polygons
        "-skipfailures",       # drop individual features that still can't be written (logged)
        "-lco", f"COORDINATE_PRECISION={config.coord_precision}",
        "-lco", "RFC7946=" + ("YES" if rfc7946_yes else "NO"),
    ]
    if select is not None:
        cmd.extend(["-select", ",".join(select)])
    cmd += [
        str(dest),
        src_str,
    ]
    if layer is not None:
        cmd.append(layer)
    if where is not None:
        cmd.extend(["-where", where])
    subprocess.run(cmd, check=True, env=_ogr_env())


def split_features(
    geojson_path: Path,
    features_dir: Path,
    *,
    id_field: str,
    name_field: str,
    extra_fields: Sequence[str] | None = None,
    clear: bool = True,
    source_tag: str | None = None,
) -> list[tuple[str, ...]]:
    """Split a FeatureCollection into one Feature per file under `features_dir`.

    Returns the list of (normalized_id, name, *extras) tuples for labels.tsv.
    When `extra_fields` is given, each tuple's tail is the values of those
    property names in order (missing/null values become empty strings).
    Duplicate ids — within one input or across inputs when called with
    clear=False — are merged by promoting their geometries to a MultiPolygon
    (or MultiLine / MultiPoint). This handles upstream datasets that ship one
    logical area as multiple Features (e.g. TDWG L4 Slovakia).

    Each output file is a single Feature object (RFC 7946 §3.2), not a
    FeatureCollection, per the backend contract. Geometry is normalized on the
    way out: a GeometryCollection (which `-makevalid` can produce) is collapsed
    to its polygonal parts, and a feature left with nothing polygonal is dropped
    and reported. Plain Polygons are written through unchanged.

    `clear=True` (default) wipes existing `*.geojson` in `features_dir` first.
    Pass `clear=False` when accumulating across multiple input collections.

    `source_tag`, when set, is stamped as `properties.source` on every output
    feature. Used by multi-source builds (mrgid, tdwg, iso) so each Feature
    self-documents which upstream layer it came from — useful when one
    physical region has several authoritative variants (e.g. mrgid:2401 vs
    mrgid:8538 vs mrgid:22170, all "Baltic Sea" from IHO/LME/ICES). When a
    cross-source merge happens, the first-writer's tag wins.
    """
    extras = tuple(extra_fields or ())
    features_dir.mkdir(parents=True, exist_ok=True)
    if clear:
        for old in features_dir.glob("*.geojson"):
            old.unlink()

    with geojson_path.open("r", encoding="utf-8") as f:
        fc = json.load(f)
    if fc.get("type") != "FeatureCollection":
        raise ValueError(f"{geojson_path} is not a FeatureCollection")

    rows: list[tuple[str, ...]] = []
    dropped: list[str] = []  # ids with no polygonal geometry left after -makevalid
    seen: dict[str, Path] = {}  # normalized id → path we wrote
    for feature in fc["features"]:
        props = feature.get("properties") or {}
        if id_field not in props:
            raise KeyError(f"feature missing id field {id_field!r}: {props}")
        raw_id = props[id_field]
        raw_name = props.get(name_field, "")
        norm = normalize_id(raw_id)
        name = "" if raw_name is None else str(raw_name).strip()
        geometry = _normalize_geometry(feature.get("geometry"))
        if geometry is None:
            # -makevalid left nothing polygonal behind (degenerate sliver).
            dropped.append(norm)
            continue
        feature["geometry"] = geometry
        feature.setdefault("properties", {})["name"] = name
        if source_tag is not None:
            feature["properties"]["source"] = source_tag
        out = features_dir / f"{norm}.geojson"

        if norm in seen:
            # True logical duplicate within this build — merge geometries.
            _merge_into(seen[norm], feature)
            continue
        if out.exists():
            # On case-insensitive filesystems, two distinct ids (e.g. IHO S-23
            # `28A` and `28a`) collide on disk even though `norm` differs.
            # Detect and fail loudly — silent merging would conflate distinct
            # gazetteer areas.
            existing_id = _stored_id(out, id_field)
            if existing_id != norm:
                raise ValueError(
                    f"filename collision on case-insensitive filesystem: "
                    f"id {norm!r} would overwrite already-written {existing_id!r} "
                    f"at {out}. Build this repo on a case-sensitive volume "
                    f"(APFS-cs / ext4 / xfs)."
                )
            _merge_into(out, feature)
            continue

        with out.open("w", encoding="utf-8") as f:
            json.dump(feature, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        extra_vals = tuple(
            "" if props.get(k) is None else str(props.get(k)).strip()
            for k in extras
        )
        rows.append((norm, name, *extra_vals))
        seen[norm] = out
    if dropped:
        print(
            f"  dropped {len(dropped)} feature(s) with no polygonal geometry: "
            + ", ".join(dropped[:20])
            + ("…" if len(dropped) > 20 else "")
        )
    return rows


# Geometry dimension, used to collapse a GeometryCollection to its most
# significant members: areas beat lines beat points.
_DIMENSION = {
    "Polygon": 2, "MultiPolygon": 2,
    "LineString": 1, "MultiLineString": 1,
    "Point": 0, "MultiPoint": 0,
}
_SINGLE_FOR_DIM = {2: "Polygon", 1: "LineString", 0: "Point"}
_MULTI_FOR_DIM = {2: "MultiPolygon", 1: "MultiLineString", 0: "MultiPoint"}


def _collect_parts(geom: dict, out: dict[int, list]) -> None:
    """Recursively bucket a geometry's coordinate parts by dimension."""
    kind = geom.get("type")
    if kind == "GeometryCollection":
        for member in geom.get("geometries", []):
            _collect_parts(member, out)
        return
    dim = _DIMENSION.get(kind)
    if dim is None:
        return
    if kind.startswith("Multi"):
        out[dim].extend(geom["coordinates"])
    else:
        out[dim].append(geom["coordinates"])


def _normalize_geometry(
    geom: dict | None, *, polygonal_only: bool = True
) -> dict | None:
    """Collapse a GeometryCollection to its most significant members.

    `ogr2ogr -makevalid` can turn a self-intersecting polygon into a
    GeometryCollection of polygons plus stray lines and points (e.g. Global
    Islands `ALL_Uniq` 298709, a 5 m2 sliver, comes back as a Polygon + a
    LineString). The backend contract wants a single geometry per Feature, and
    callers otherwise write whatever ogr2ogr produced.

    With `polygonal_only` (the default, for area gazetteers) only polygons are
    kept and a collection with none returns None, so the caller can drop the
    feature. Pass `polygonal_only=False` for a gazetteer that legitimately
    carries lines or points — `mrgid` ships Point centroids and line features —
    to fall back to lines, then points, rather than discarding the geometry.

    Nested GeometryCollections are flattened. Anything that is not a
    GeometryCollection is returned unchanged — in particular a Polygon stays a
    Polygon and is *not* promoted to MultiPolygon, which would rewrite every
    already-committed feature tree.
    """
    if geom is None or geom.get("type") != "GeometryCollection":
        return geom
    parts: dict[int, list] = {2: [], 1: [], 0: []}
    _collect_parts(geom, parts)
    for dim in (2, 1, 0) if not polygonal_only else (2,):
        if parts[dim]:
            if len(parts[dim]) == 1:
                return {"type": _SINGLE_FOR_DIM[dim], "coordinates": parts[dim][0]}
            return {"type": _MULTI_FOR_DIM[dim], "coordinates": parts[dim]}
    return None


def _stored_id(path: Path, id_field: str) -> str:
    """Read the id that was stored in a previously-written feature file."""
    with path.open("r", encoding="utf-8") as f:
        feat = json.load(f)
    props = feat.get("properties") or {}
    raw = props.get(id_field, "")
    return normalize_id(raw) if raw != "" else ""


def _merge_into(existing_path: Path, incoming: dict) -> None:
    """Combine `incoming`'s geometry into the feature on disk, promoting to
    a Multi* type. Properties are kept from the existing feature."""
    with existing_path.open("r", encoding="utf-8") as f:
        existing = json.load(f)
    merged_geom = _merge_geometries(existing.get("geometry"), incoming.get("geometry"))
    existing["geometry"] = merged_geom
    with existing_path.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


_MULTI_OF = {
    "Polygon": "MultiPolygon",
    "MultiPolygon": "MultiPolygon",
    "LineString": "MultiLineString",
    "MultiLineString": "MultiLineString",
    "Point": "MultiPoint",
    "MultiPoint": "MultiPoint",
}


def _flatten(geom: dict) -> tuple[str | None, list]:
    """Return (multi_type, list of part-coordinates) for any geometry.

    GeometryCollection is flattened to MultiPolygon, keeping only its
    polygonal parts — `ogr2ogr -makevalid` can split self-intersecting input
    into a mix of polygons, lines and points; for gazetteer areas we only
    want the polygons. Returns `(None, [])` when the input has no polygonal
    parts (e.g. a degenerate GeometryCollection of lines + points), so the
    caller can drop it.
    """
    t = geom["type"]
    if t in _MULTI_OF:
        if t.startswith("Multi"):
            return _MULTI_OF[t], list(geom["coordinates"])
        return _MULTI_OF[t], [geom["coordinates"]]
    if t == "GeometryCollection":
        polys: list = []
        for g in geom.get("geometries", []):
            if g["type"] == "Polygon":
                polys.append(g["coordinates"])
            elif g["type"] == "MultiPolygon":
                polys.extend(g["coordinates"])
        return ("MultiPolygon", polys) if polys else (None, [])
    raise ValueError(f"unsupported geometry type: {t}")


def _merge_geometries(a: dict | None, b: dict | None) -> dict:
    if a is None or b is None:
        raise ValueError("cannot merge null geometry")
    ta, parts_a = _flatten(a)
    tb, parts_b = _flatten(b)
    # Drop empty / non-polygonal contributions silently.
    if ta is None:
        if tb is None:
            raise ValueError("cannot merge: neither geometry has polygonal parts")
        return {"type": tb, "coordinates": parts_b}
    if tb is None:
        return {"type": ta, "coordinates": parts_a}
    if ta != tb:
        raise ValueError(f"cannot merge geometries: {a['type']} + {b['type']}")
    return {"type": ta, "coordinates": parts_a + parts_b}


