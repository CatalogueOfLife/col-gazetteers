"""Subdivision outlines from OpenStreetMap, through Overpass.

Per country, every relation tagged `ISO3166-2=<CC>-…`; and, for ids whose
Wikidata item links an OSM relation (P402) but carry no such tag, those
relations by id. Overpass returns full member geometry (`out geom`), which
osm2geojson assembles into polygons.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path

import osm2geojson
import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from common.download import SourceRecord
from common.overpass import fetch as overpass_fetch

from .geom import polys
from .model import Candidate

BATCH = 20  # relations per query
# Countries per query. Each Overpass request costs about a minute of queueing, whatever its size.
COUNTRY_BATCH = 8
_HEADER = "[out:json][timeout:900][maxsize:2147483648];"


def countries_query(ccs: Sequence[str]) -> str:
    return f'{_HEADER}relation["ISO3166-2"~"^({"|".join(ccs)})-"];out geom;'


def relations_query(ids: Sequence[int]) -> str:
    return f"{_HEADER}relation(id:{','.join(str(i) for i in ids)});out geom;"


def fetch_osm(countries: Iterable[str], relation_ids: Iterable[int], sources_dir: Path, *,
              force: bool, batch: int = COUNTRY_BATCH) -> tuple[list[dict], list[SourceRecord]]:
    """The raw Overpass documents. Fails, naming them, if any country or batch of relations failed.

    A batch of countries that fails, often because its answer is too large for
    Overpass's gateway, is split in half and tried again, down to single countries.
    """
    target = sources_dir / "osm"
    docs, records, failed = [], [], []
    ccs = sorted(set(countries))
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
        except RuntimeError as e:
            if isinstance(job, list) and len(job) > 1:
                half = len(job) // 2
                print(f"[iso] Overpass failed for {label} ({e}); trying {','.join(job[:half])} and {','.join(job[half:])}")
                jobs[:0] = [job[:half], job[half:]]
                continue
            print(f"[iso] Overpass failed for {label}: {e}")
            failed.append(label)
            continue
        docs.append(json.loads(Path(path).read_text(encoding="utf-8")))
        if record is not None:
            records.append(record)
    if failed:
        raise RuntimeError(f"Overpass failed for {', '.join(failed)}")
    return docs, records


def unwrap_antimeridian(geom: BaseGeometry) -> BaseGeometry:
    """Split polygon parts written across ±180 (wider than 180°) back onto both sides of it."""
    parts = []
    for part in shapely.get_parts(geom):
        x0, _, x1, _ = part.bounds
        if x1 - x0 <= 180:
            parts.append(part)
            continue
        shifted = shapely.transform(part, lambda c: c + [[360, 0]] * (c[:, :1] < 0))
        parts.append(shapely.clip_by_rect(shifted, 0, -90, 180, 90))
        east = shapely.clip_by_rect(shifted, 180, -90, 360, 90)
        parts.append(shapely.transform(east, lambda c: c - [360, 0]))
    return polys(shapely.union_all(parts)) if len(parts) > 1 else polys(parts[0]) if parts else polys(None)


def _relation_geoms(raw: dict):
    """(relation id, tags, geometry) for every relation osm2geojson could assemble."""
    for feature in osm2geojson.json2geojson(raw).get("features", []):
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


def to_candidates(raw: dict) -> dict[str, Candidate]:
    """ISO 3166-2 code -> the best relation tagged with it."""
    best: dict[str, tuple[tuple, Candidate]] = {}
    for rel_id, tags, geom in _relation_geoms(raw):
        level = tags.get("admin_level", "")
        rank = (0 if tags.get("boundary") == "administrative" else 1, int(level) if level.isdigit() else 99, -geom.area)
        candidate = Candidate(geom, f"osm-relation-{rel_id}", "osm",
                              label=tags.get("name:en") or tags.get("name", ""))
        for code in tags.get("ISO3166-2", "").replace(",", ";").split(";"):
            code = code.strip()
            if code and (code not in best or rank < best[code][0]):
                best[code] = (rank, candidate)
    return {code: candidate for code, (_, candidate) in best.items()}


def by_relation(raw: dict) -> dict[int, BaseGeometry]:
    return {rel_id: geom for rel_id, _, geom in _relation_geoms(raw)}
