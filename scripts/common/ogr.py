"""ogr2ogr wrappers — shapefile/zip → simplified GeoJSON FeatureCollection."""

from __future__ import annotations

import json
import os
import subprocess
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
    src: Path,
    dest: Path,
    config: Config,
    *,
    layer: str | None = None,
    where: str | None = None,
) -> None:
    """Reproject + simplify a shapefile (or any OGR source) into one GeoJSON.

    `src` may be a `.shp` path or `/vsizip/path/to/foo.zip/inner.shp`.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    cmd = [
        "ogr2ogr",
        "-f", "GeoJSON",
        "-t_srs", config.epsg,
        "-simplify", str(config.simplify_tolerance),
        "-makevalid",          # repair self-intersecting / side-location-conflict polygons
        "-skipfailures",       # drop individual features that still can't be written (logged)
        "-lco", f"COORDINATE_PRECISION={config.coord_precision}",
        "-lco", "RFC7946=" + ("YES" if config.crs == "4326" else "NO"),
        str(dest),
        str(src),
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
    clear: bool = True,
) -> list[tuple[str, str]]:
    """Split a FeatureCollection into one Feature per file under `features_dir`.

    Returns the list of (normalized_id, name) pairs for labels.tsv. Duplicate
    ids — within one input or across inputs when called with clear=False —
    are merged by promoting their geometries to a MultiPolygon (or MultiLine /
    MultiPoint). This handles upstream datasets that ship one logical area as
    multiple Features (e.g. TDWG L4 Slovakia).

    Each output file is a single Feature object (RFC 7946 §3.2), not a
    FeatureCollection, per the backend contract.

    `clear=True` (default) wipes existing `*.geojson` in `features_dir` first.
    Pass `clear=False` when accumulating across multiple input collections.
    """
    features_dir.mkdir(parents=True, exist_ok=True)
    if clear:
        for old in features_dir.glob("*.geojson"):
            old.unlink()

    with geojson_path.open("r", encoding="utf-8") as f:
        fc = json.load(f)
    if fc.get("type") != "FeatureCollection":
        raise ValueError(f"{geojson_path} is not a FeatureCollection")

    rows: list[tuple[str, str]] = []
    seen: dict[str, Path] = {}  # normalized id → path we wrote
    for feature in fc["features"]:
        props = feature.get("properties") or {}
        if id_field not in props:
            raise KeyError(f"feature missing id field {id_field!r}: {props}")
        raw_id = props[id_field]
        raw_name = props.get(name_field, "")
        norm = normalize_id(raw_id)
        name = "" if raw_name is None else str(raw_name).strip()
        feature.setdefault("properties", {})["name"] = name
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
        rows.append((norm, name))
        seen[norm] = out
    return rows


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


def _merge_geometries(a: dict | None, b: dict | None) -> dict:
    if a is None or b is None:
        raise ValueError("cannot merge null geometry")
    multi_a = _MULTI_OF.get(a["type"])
    multi_b = _MULTI_OF.get(b["type"])
    if multi_a is None or multi_b is None or multi_a != multi_b:
        raise ValueError(f"cannot merge geometries: {a['type']} + {b['type']}")
    parts_a = a["coordinates"] if a["type"].startswith("Multi") else [a["coordinates"]]
    parts_b = b["coordinates"] if b["type"].startswith("Multi") else [b["coordinates"]]
    return {"type": multi_a, "coordinates": parts_a + parts_b}


