"""Invariants of a written iso tree. The build refuses to finish when any fails."""

from __future__ import annotations

import json
from pathlib import Path

import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from .geom import clip_to_box, polys

COUNTRY_SLACK = 0.001  # share of a country's land that may lie outside its land + EEZ (and the band)
SUBDIVISION_SLACK = 0.01
# Simplifying land, land + EEZ and subdivisions separately at 0.005° moves their edges apart by up to
# about that much, which is a large share of Monaco or a Liechtenstein municipality. Containment is
# therefore checked against the containing area widened by twice the tolerance.
BAND = 0.01


def _read(path: Path) -> BaseGeometry:
    return polys(shape(json.loads(path.read_text(encoding="utf-8"))["geometry"]))


def _outside(geom: BaseGeometry, area: BaseGeometry) -> float:
    """Area of `geom` outside `area` widened by BAND."""
    x0, y0, x1, y1 = geom.bounds
    local = clip_to_box(area, x0 - BAND, y0 - BAND, x1 + BAND, y1 + BAND)
    return geom.difference(local.buffer(BAND)).area


def _table(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


def check_tree(iso_dir: Path, floor_ids: set[str] | None = None) -> list[str]:
    land_dir, eez_dir = iso_dir / "features", iso_dir / "eez" / "features"
    labels = {line.split("\t")[0] for line in (iso_dir / "labels.tsv").read_text(encoding="utf-8").splitlines() if line}
    problems = [f"{code} from the previous labels.tsv is missing" for code in sorted((floor_ids or set()) - labels)]

    for d in (land_dir, eez_dir):
        problems += [f"{p.relative_to(iso_dir)} points to a missing file"
                     for p in sorted(d.glob("*.geojson")) if p.is_symlink() and not p.resolve().is_file()]

    countries = sorted(code for code in labels if len(code) == 2 and code != "XZ")
    for code in countries:
        land, eez = land_dir / f"{code}.geojson", eez_dir / f"{code}.geojson"
        if not land.exists() or not eez.exists():
            problems.append(f"{code} lacks its {'land' if not land.exists() else 'land + EEZ'} shape")
            continue
        if land.is_symlink():
            continue
        g_land, g_eez = _read(land), _read(eez)
        outside = _outside(g_land, g_eez)
        if outside > COUNTRY_SLACK * g_land.area:
            problems.append(f"{code}: {outside / g_land.area:.1%} of its land lies outside its land + EEZ")

    rows = _table(iso_dir / "sources.tsv")
    territories: dict[str, set[str]] = {}
    for row in rows:
        if row["resolution"] == "alias-dual":
            territories.setdefault(row["id"].split("-")[0], set()).add(row["target"])
    areas: dict[str, BaseGeometry] = {}
    for row in rows:
        code = row["id"]
        if "-" not in code or row["resolution"].startswith("alias") or row["fit"] in ("", "conflict"):
            continue
        parent = code.split("-")[0]
        if parent not in areas:
            parts = [_read(eez_dir / f"{c}.geojson") for c in {parent} | territories.get(parent, set())
                     if (eez_dir / f"{c}.geojson").exists()]
            areas[parent] = polys(shapely.union_all(parts)) if parts else polys(None)
        if areas[parent].is_empty:
            continue  # historic parents (CS, YU) have no shape of their own
        geom = _read(land_dir / f"{code}.geojson")
        if geom.is_empty:
            problems.append(f"{code} has an empty shape")
            continue
        outside = _outside(geom, areas[parent])
        if outside > SUBDIVISION_SLACK * geom.area:
            problems.append(f"{code}: {outside / geom.area:.1%} lies outside {parent}'s land + EEZ")
    return problems
