"""Subdivision shapes from Natural Earth's admin-1 layer, keyed by its iso_3166_2 field."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import shapely
from shapely.geometry import shape

from common.download import SourceRecord, download

from .geom import polys
from .model import Candidate
from .universe import ID_PATTERN

URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip"
SHP = "ne_10m_admin_1_states_provinces.shp"


def read_natural_earth(src: Path | str, work_dir: Path) -> dict[str, Candidate]:
    """id -> candidate for every feature whose iso_3166_2 is a subdivision code."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "ne_admin_1.geojson"
    out.unlink(missing_ok=True)
    subprocess.run(["ogr2ogr", "-f", "GeoJSON", "-makevalid", str(out), str(src)], check=True)
    parts: dict[str, list] = {}
    labels: dict[str, str] = {}
    for feature in json.loads(out.read_text(encoding="utf-8"))["features"]:
        props = feature["properties"] or {}
        code = (props.get("iso_3166_2") or "").strip()
        if "-" not in code or not ID_PATTERN.match(code) or not feature["geometry"]:
            continue
        parts.setdefault(code, []).append(polys(shape(feature["geometry"])))
        labels.setdefault(code, props.get("name_en") or props.get("name") or "")
    return {
        code: Candidate(polys(shapely.union_all(geoms)), "natural-earth-admin-1", "natural-earth", label=labels[code])
        for code, geoms in parts.items()
    }


def natural_earth_candidates(sources_dir: Path, work_dir: Path, *, force: bool) -> tuple[dict[str, Candidate], SourceRecord]:
    path, record = download(
        URL, sources_dir, role="natural-earth-admin-1", name="Natural Earth 10m: ne_10m_admin_1_states_provinces",
        filename="ne_10m_admin_1_states_provinces.zip", force=force, upstream_version="10m", timeout=600,
    )
    return read_natural_earth(f"/vsizip/{path}/{SHP}", work_dir), record
