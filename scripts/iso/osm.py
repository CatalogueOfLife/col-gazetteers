"""Subdivision outlines from OpenStreetMap, through Overpass.

Per country, every relation tagged `ISO3166-2=<CC>-…`; and, for ids whose
Wikidata item links an OSM relation (P402) but carry no such tag, those
relations by id. Overpass returns full member geometry (`out geom`), which
osm2geojson assembles into polygons.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
import osm2geojson
import shapely
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from common.download import SourceRecord
from common.overpass import fetch as overpass_fetch

from .geom import clip_to_box, polys
from .model import Candidate

BATCH = 20  # relations per query
# Countries per query. Each Overpass request costs about a minute of queueing, whatever its size.
COUNTRY_BATCH = 8
_HEADER = "[out:json][timeout:900][maxsize:2147483648];"

# The snapshot the monthly build reads instead of Overpass; refresh_osm.py writes it.
SNAPSHOT = "osm-snapshot.geojson.gz"
SNAPSHOT_TOLERANCE = 0.0005  # ~50 m, ten times finer than the published 0.005°
_SNAPSHOT_TAGS = ("ISO3166-2", "boundary", "admin_level", "name", "name:en", "wikidata")


def truncated(path: Path) -> bool:
    """Whether an Overpass answer ends in a runtime-error remark (a timeout or out of memory),
    which comes with HTTP 200 and an incomplete list of elements."""
    with path.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 4096))
        tail = f.read().decode("utf-8", "replace")
    return '"remark"' in tail and "runtime error" in tail


def cached_answers(target: Path) -> list[Path]:
    """Overpass answers an earlier run left in `target`, for countries and for relations."""
    return sorted([*target.glob("countries_*.json"), *target.glob("relations_*.json")])


def countries_query(ccs: Sequence[str]) -> str:
    return f'{_HEADER}relation["ISO3166-2"~"^({"|".join(ccs)})-"];out geom;'


def relations_query(ids: Sequence[int]) -> str:
    return f"{_HEADER}relation(id:{','.join(str(i) for i in ids)});out geom;"


def fetch_osm(countries: Iterable[str], relation_ids: Iterable[int], sources_dir: Path, *,
              force: bool, batch: int = COUNTRY_BATCH) -> tuple[list[Path], list[SourceRecord]]:
    """The files of the raw Overpass documents. Fails, naming them, if any country or batch of relations failed.

    A batch of countries that fails, often because its answer is too large for
    Overpass's gateway, is split in half and tried again, down to single countries.
    """
    target = sources_dir / "osm"
    paths, records, failed = [], [], []
    ccs = sorted(set(countries))
    if not force:
        # Answers cached by an earlier, interrupted run, possibly for split batches: use them as they are.
        for path in cached_answers(target):
            group = path.stem.removeprefix("countries_").split("-")
            if truncated(path):
                path.unlink()
                continue
            if path.stem.startswith("countries_") and set(group) <= set(ccs):
                paths.append(path)
                ccs = [cc for cc in ccs if cc not in group]
    # A job is a list of countries, or ("relations", [ids]).
    jobs: list[list[str] | tuple[str, list[int]]] = [ccs[i:i + batch] for i in range(0, len(ccs), batch)]
    ids = sorted(set(relation_ids))
    jobs += [("relations", ids[i:i + BATCH]) for i in range(0, len(ids), BATCH)]
    while jobs:
        job = jobs.pop(0)
        if isinstance(job, tuple):
            chunk = job[1]
            digest = hashlib.sha1(",".join(map(str, chunk)).encode()).hexdigest()[:10]
            label, query, cache_path = f"relations {chunk[0]}…{chunk[-1]}", relations_query(chunk), target / f"relations_{digest}.json"
            role, name = "osm-overpass-relations", f"OSM Overpass: {len(chunk)} relations linked from Wikidata"
        else:
            label, query, cache_path = ",".join(job), countries_query(job), target / f"countries_{'-'.join(job)}.json"
            role, name = f"osm-overpass-{label.lower().replace(',', '-')}", f"OSM Overpass: relations tagged ISO3166-2 in {label}"
        try:
            path, record = overpass_fetch(query, cache_path, role=role, name=name, force=force)
            if truncated(Path(path)):
                Path(path).unlink()
                raise RuntimeError("Overpass answered with a runtime error, its answer is incomplete")
        except RuntimeError as e:
            if isinstance(job, list) and len(job) > 1:
                half = len(job) // 2
                print(f"[iso] Overpass failed for {label} ({e}); trying {','.join(job[:half])} and {','.join(job[half:])}")
                jobs[:0] = [job[:half], job[half:]]
                continue
            print(f"[iso] Overpass failed for {label}: {e}")
            failed.append(label)
            continue
        paths.append(Path(path))
        if record is not None:
            records.append(record)
    if failed:
        raise RuntimeError(f"Overpass failed for {', '.join(failed)}")
    return paths, records


def unwrap_antimeridian(geom: BaseGeometry) -> BaseGeometry:
    """Put polygon parts that cross ±180 back on both sides of it: parts written across it
    (wider than 180°) and parts shifted east of it by `shift_across_antimeridian`."""
    parts = []
    for part in shapely.get_parts(geom):
        x0, _, x1, _ = part.bounds
        if x1 - x0 > 180:
            part = shapely.transform(part, lambda c: c + [[360, 0]] * (c[:, :1] < 0))
            x1 = part.bounds[2]
        if x1 <= 180:
            parts.append(part)
            continue
        parts.append(clip_to_box(part, -180, -90, 180, 90))
        east = clip_to_box(part, 180, -90, 540, 90)
        parts.append(shapely.transform(east, lambda c: c - [360, 0]))
    return polys(shapely.union_all(parts)) if len(parts) > 1 else polys(parts[0]) if parts else polys(None)


def _crosses_antimeridian(element: dict) -> bool:
    for member in element.get("members") or ():
        lons = [p["lon"] for p in member.get("geometry") or () if p]
        if any(abs(a - b) > 180 for a, b in zip(lons, lons[1:])):
            return True
    return False


def shift_across_antimeridian(raw: dict) -> dict:
    """Overpass answer with every relation that crosses ±180 moved east, negative longitudes
    plus 360, so that osm2geojson can close its rings: it drops rings that cross themselves,
    as a ring written across ±180 often does. `unwrap_antimeridian` moves them back."""
    elements = []
    for element in raw.get("elements", []):
        if element.get("type") == "relation" and _crosses_antimeridian(element):
            element = {**element, "members": [
                {**m, "geometry": [p and {**p, "lon": p["lon"] + 360 if p["lon"] < 0 else p["lon"]}
                                   for p in m["geometry"]]} if m.get("geometry") else m
                for m in element["members"]]}
        elements.append(element)
    return {**raw, "elements": elements}


def relation_geoms(raw: dict):
    """(relation id, tags, geometry) for every relation osm2geojson could assemble."""
    for feature in osm2geojson.json2geojson(shift_across_antimeridian(raw)).get("features", []):
        props = feature.get("properties") or {}
        if props.get("type") != "relation" or not feature.get("geometry"):
            continue
        try:
            geom = unwrap_antimeridian(polys(shape(feature["geometry"])))
        except Exception as e:  # a relation osm2geojson assembled into something shapely can't repair
            print(f"[iso] skipping OSM relation {props.get('id')}: {e}")
            continue
        if not geom.is_empty:
            yield int(props["id"]), props.get("tags") or {}, geom


def candidates_from(items: Iterable[tuple[int, dict, BaseGeometry]]) -> dict[str, Candidate]:
    """ISO 3166-2 code -> the best of the relations tagged with it."""
    best: dict[str, tuple[tuple, Candidate]] = {}
    for rel_id, tags, geom in items:
        level = tags.get("admin_level", "")
        rank = (0 if tags.get("boundary") == "administrative" else 1, int(level) if level.isdigit() else 99, -geom.area)
        candidate = Candidate(geom, f"osm-relation-{rel_id}", "osm",
                              label=tags.get("name:en") or tags.get("name", ""))
        for code in tags.get("ISO3166-2", "").replace(",", ";").split(";"):
            code = code.strip()
            if code and (code not in best or rank < best[code][0]):
                best[code] = (rank, candidate)
    return {code: candidate for code, (_, candidate) in best.items()}


def to_candidates(raw: dict) -> dict[str, Candidate]:
    """ISO 3166-2 code -> the best relation of an Overpass document tagged with it."""
    return candidates_from(relation_geoms(raw))


def by_relation(raw: dict) -> dict[int, BaseGeometry]:
    return {rel_id: geom for rel_id, _, geom in relation_geoms(raw)}


def write_snapshot(items: Iterable[tuple[int, dict, BaseGeometry]], path: Path, *,
                   tolerance: float = SNAPSHOT_TOLERANCE, countries: Iterable[str] = (),
                   source: str = "overpass") -> int:
    """Each relation once, lightly simplified, as gzipped GeoJSON. Returns how many were written.

    `countries` are those the source covers; the build refuses a snapshot lacking any.
    `source` says where the relations come from: `overpass`, or `overture <release>`.
    """
    relations: dict[int, tuple[dict, BaseGeometry]] = {}
    for rel_id, tags, geom in items:
        relations.setdefault(rel_id, (tags, geom))
    features = []
    for rel_id, (tags, geom) in sorted(relations.items()):
        geom = polys(shapely.simplify(geom, tolerance, preserve_topology=True))
        geom = shapely.transform(geom, lambda c: np.round(c, 6))
        features.append({"type": "Feature", "id": rel_id,
                         "properties": {"id": rel_id, "tags": {k: tags[k] for k in _SNAPSHOT_TAGS if k in tags}},
                         "geometry": mapping(geom)})
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "source": source, "countries": sorted(set(countries)),
                   "features": features}, f,
                  separators=(",", ":"))
    return len(features)


def snapshot_source(path: Path) -> str:
    """Where a snapshot's relations come from; snapshots that don't say came from Overpass."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f).get("source", "overpass")


def snapshot_missing(path: Path, wanted: Iterable[str]) -> list[str]:
    """The countries of `wanted` that the snapshot holds no Overpass answer for."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        recorded = set(json.load(f).get("countries", []))
    return sorted(set(wanted) - recorded)


def read_snapshot(path: Path) -> list[tuple[int, dict, BaseGeometry]]:
    """(relation id, tags, geometry) of every relation in a snapshot."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        features = json.load(f)["features"]
    items = []
    for feature in features:
        geom = polys(shape(feature["geometry"])) if feature.get("geometry") else polys(None)
        if not geom.is_empty:
            items.append((int(feature["properties"]["id"]), feature["properties"].get("tags") or {}, geom))
    return items
