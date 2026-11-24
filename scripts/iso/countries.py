"""Each country's two shapes: land, and land + EEZ.

Land + EEZ is the union of every row that counts for the code. Land is the same
rows with the EEZ v12 water taken away, at full resolution, so that land always
lies inside land + EEZ.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import shapely
from shapely import STRtree
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from .geom import polys
from .model import Countries, Row

HISTORIC = {"YU": ("SI", "HR", "BA", "RS", "ME", "MK", "XK"), "AN": ("CW", "SX", "BQ")}

# Set before forking, so the workers share the water instead of pickling it.
_WATER: Sequence[BaseGeometry] = ()
_TREE: STRtree | None = None
_GEOMS: Sequence[BaseGeometry] = ()


def read_water(eez_gpkg: Path, work_dir: Path) -> list[BaseGeometry]:
    """The EEZ v12 polygons, repaired, not simplified."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "eez_v12.geojson"
    out.unlink(missing_ok=True)
    subprocess.run(["ogr2ogr", "-f", "GeoJSON", "-makevalid", str(out), str(eez_gpkg)], check=True)
    features = json.loads(out.read_text(encoding="utf-8"))["features"]
    water = [polys(shape(f["geometry"])) for f in features if f["geometry"]]
    return [w for w in water if not w.is_empty]


def land_of(geom: BaseGeometry, water: Sequence[BaseGeometry], tree: STRtree) -> BaseGeometry:
    """`geom` without the water polygons that touch it."""
    hits = tree.query(geom, predicate="intersects")
    if len(hits) == 0:
        return geom
    return polys(geom.difference(shapely.union_all([water[i] for i in hits])))


def _is_all_land(row: Row) -> bool:
    if row.get("pol_type") == "Landlocked country":
        return True
    return row.get("union") == "Antarctica" and row.get("pol_type") == "Added by GBIF"


def _land_worker(i: int) -> tuple[int, bytes]:
    return i, shapely.to_wkb(land_of(_GEOMS[i], _WATER, _TREE))


def build_countries(rows: list[Row], water: list[BaseGeometry], *, workers: int = os.cpu_count() or 1) -> Countries:
    global _WATER, _TREE, _GEOMS
    lands: list[BaseGeometry | None] = [r["geom"] if _is_all_land(r) else None for r in rows]
    todo = [i for i, land in enumerate(lands) if land is None]
    _WATER, _TREE, _GEOMS = water, STRtree(water), [r["geom"] for r in rows]
    if workers > 1 and len(todo) > 1:
        # Biggest first, so the slow rows don't end up last.
        todo.sort(key=lambda i: -shapely.get_num_coordinates(_GEOMS[i]))
        with mp.get_context("fork").Pool(workers) as pool:
            for i, wkb in pool.imap_unordered(_land_worker, todo):
                lands[i] = shapely.from_wkb(wkb)
    else:
        for i in todo:
            lands[i] = land_of(_GEOMS[i], _WATER, _TREE)
    _WATER, _TREE, _GEOMS = (), None, ()

    eez_parts: dict[str, list[BaseGeometry]] = {}
    land_parts: dict[str, list[BaseGeometry]] = {}
    result = Countries()
    for row, land in zip(rows, lands):
        for code in row["codes"]:
            eez_parts.setdefault(code, []).append(row["geom"])
            land_parts.setdefault(code, []).append(land)
            result.rows.setdefault(code, []).append(f"{row.get('union')} ({row.get('pol_type')})")
    for code in eez_parts:
        result.eez[code] = polys(shapely.union_all(eez_parts[code]))
        result.land[code] = polys(shapely.union_all(land_parts[code]))
    return result


def add_historic(countries: Countries) -> None:
    """YU and AN, dissolved from their successors, in both shapes."""
    for code, successors in HISTORIC.items():
        countries.land[code] = polys(shapely.union_all([countries.land[s] for s in successors if s in countries.land]))
        countries.eez[code] = polys(shapely.union_all([countries.eez[s] for s in successors if s in countries.eez]))
        countries.rows[code] = [f"dissolved from {', '.join(successors)}"]
