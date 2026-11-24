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

BATCH = 20


def country_query(cc: str) -> str:
    return f'[out:json][timeout:900];relation["ISO3166-2"~"^{cc}-"];out geom;'


def relations_query(ids: Sequence[int]) -> str:
    return f"[out:json][timeout:900];relation(id:{','.join(str(i) for i in ids)});out geom;"


def fetch_osm(countries: Iterable[str], relation_ids: Iterable[int], sources_dir: Path, *,
              force: bool) -> tuple[list[dict], list[SourceRecord]]:
    """The raw Overpass documents. Fails, naming them, if any country or batch could not be fetched."""
    target = sources_dir / "osm"
    docs, records, failed = [], [], []
    jobs = [(cc, country_query(cc), target / f"{cc}.json", f"osm-overpass-{cc.lower()}",
             f"OSM Overpass: relations tagged ISO3166-2={cc}-…") for cc in countries]
    ids = sorted(set(relation_ids))
    for i in range(0, len(ids), BATCH):
        batch = ids[i:i + BATCH]
        digest = hashlib.sha1(",".join(map(str, batch)).encode()).hexdigest()[:10]
        jobs.append((f"relations {batch[0]}…{batch[-1]}", relations_query(batch), target / f"relations_{digest}.json",
                     "osm-overpass-relations", f"OSM Overpass: {len(batch)} relations linked from Wikidata"))
    for label, query, cache_path, role, name in jobs:
        try:
            path, record = overpass_fetch(query, cache_path, role=role, name=name, force=force)
        except RuntimeError as e:
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
