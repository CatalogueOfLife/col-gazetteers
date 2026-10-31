# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scikit-image", "shapely"]
# ///
"""Trace the Ural and Greater Caucasus drainage divides on the Copernicus DEM.

Run this by hand when the divides need redrawing. It is not part of a rebuild:
the result, scripts/continent/ridges.geojson, is committed, so that a rebuild
needs no elevation data.

    uv run scripts/continent/trace_ridges.py

A divide is found by flooding the elevation model from two sets of markers, one
for each side (skimage's marker-based watershed). Water rises from the lowest
ground first, so a valley joins the side it drains to, and the two floods meet
on the divide. Unlike a path between hand-picked peaks, this needs no waypoints
and cannot wander off the crest where a range is low, as the Middle Urals are.

The markers are the lowlands along the outer edge of each corridor and the seas
each side drains to. Where a divide ends at the coast is set by where the sea is
split between the two sides (`RIDGES`).

DEM: Copernicus WorldDEM-90 (GLO-90). See ATTRIBUTIONS.md for its notice.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import shapely
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from shapely.geometry import LineString
from skimage.measure import find_contours
from skimage.segmentation import watershed

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "ridges.geojson"
CACHE = HERE.parent.parent / "work" / "continent" / "dem"

TILE_URL = (
    "https://copernicus-dem-90m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_30_{lat}_00_{lon}_00_DEM/Copernicus_DSM_COG_30_{lat}_00_{lon}_00_DEM.tif"
)
# Cells per degree: 0.005°, about 500 m N-S. The tolerance the features end up at.
CELLS = 200
SIDE_A, SIDE_B = 1, 2  # the Europe side, the Asia side
UNSEEN = -9999.0  # cells outside the corridor, which are markers instead
SIMPLIFY = 0.005
COORD_DECIMALS = 4


@dataclass(frozen=True)
class Ridge:
    """A divide to trace, within the grid `west`..`east` x `south`..`north`."""

    name: str
    west: int
    east: int
    south: int
    north: int
    # Where the line starts and ends (lon, lat). Each is snapped to the divide.
    start: tuple[float, float]
    end: tuple[float, float]
    # The corridor at a latitude: (lon of its west edge, lon of its east edge).
    corridor: Callable[[float], tuple[float, float]]
    # Marks cells of the corridor that are known to be on one side, given the
    # elevation, longitude and latitude arrays: 0 for neither.
    markers: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]


def _ural_corridor(lat: float) -> tuple[float, float]:
    # Wide of the crest throughout: the Polar Urals bend away to the north-east.
    return (60.0, 70.0) if lat >= 65.5 else (58.0, 65.0) if lat >= 64 else (57.0, 62.0)


def _ural_markers(z: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """The Kara Sea is split at Baydaratskaya Bay: the coast west of it, with
    the Pay-Khoy ridge, stays on the European side of the Urals. Inland, the Ob
    at Salekhard is as low as the sea, so only the far north counts as sea."""
    out = np.zeros(z.shape, dtype=np.int32)
    sea = (z <= 0.5) & (lat > 68.6)
    out[sea & (lon < 67.2)] = SIDE_A
    out[sea & (lon >= 67.2)] = SIDE_B
    return out


def _caucasus_corridor(_lat: float) -> tuple[float, float]:
    return (36.0, 51.0)


def _caucasus_markers(z: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """North of the divide: Sea of Azov, the Black Sea off the Kuban delta and
    the Caspian north of Absheron. South of it: the rest of those seas. The top
    and bottom rows of the grid are the steppe and the Lesser Caucasus."""
    out = np.zeros(z.shape, dtype=np.int32)
    black_sea = (z <= 0.5) & (lon < 42.0)
    north_of_anapa = lat > 44.92
    out[black_sea & north_of_anapa] = SIDE_A
    out[black_sea & ~north_of_anapa] = SIDE_B
    caspian = (z <= -26) & (lon > 47.3)
    out[caspian & (lat > 40.72)] = SIDE_A
    out[caspian & (lat <= 40.72)] = SIDE_B
    out[0, :] = SIDE_B  # row 0 is the south edge
    out[-1, :] = SIDE_A
    return out


RIDGES = [
    Ridge(
        "ural-crest", west=55, east=71, south=53, north=70,
        # From the Kara Sea to the head of the Ural River (see build.py), north to south.
        start=(67.2, 69.0), end=(59.45, 54.7),
        corridor=_ural_corridor, markers=_ural_markers,
    ),
    Ridge(
        "caucasus-crest", west=36, east=51, south=40, north=46,
        # From the Caspian north of Absheron to the Black Sea at Anapa, east to west.
        start=(49.6, 40.72), end=(37.3, 44.92),
        corridor=_caucasus_corridor, markers=_caucasus_markers,
    ),
]


def _tile_url(lat: int, lon: int) -> str:
    ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
    ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
    return TILE_URL.format(lat=ns, lon=ew)


def read_tile(lat: int, lon: int) -> np.ndarray:
    """A 1° tile at CELLS per degree, south-up. A tile that doesn't exist is sea."""
    cache = CACHE / f"{lat}_{lon}_{CELLS}.npy"
    if cache.exists():
        return np.load(cache)
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            with rasterio.open(_tile_url(lat, lon)) as src:
                grid = src.read(1, out_shape=(CELLS, CELLS), resampling=Resampling.average)
    except RasterioIOError:
        grid = np.zeros((CELLS, CELLS), dtype=np.float32)
    grid = np.flipud(grid).astype(np.float32)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, grid)
    return grid


def read_grid(ridge: Ridge) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The corridor's elevation, with cells beyond its edges already marked.

    Returns (elevation, markers, lon, lat), rows running south to north.
    """
    rows = (ridge.north - ridge.south) * CELLS
    cols = (ridge.east - ridge.west) * CELLS
    lon = ridge.west + (np.arange(cols) + 0.5) / CELLS
    lat = ridge.south + (np.arange(rows) + 0.5) / CELLS
    lon2d, lat2d = np.meshgrid(lon, lat)
    z = np.full((rows, cols), UNSEEN, dtype=np.float32)
    outer = np.zeros((rows, cols), dtype=np.int32)

    edges = np.array([ridge.corridor(float(v)) for v in lat])
    outer[lon2d < edges[:, 0][:, None]] = SIDE_A
    outer[lon2d >= edges[:, 1][:, None]] = SIDE_B

    needed = [
        (la, lo)
        for la in range(ridge.south, ridge.north)
        for lo in range(ridge.west, ridge.east)
        if any(
            lo + 1 > ridge.corridor(la + f)[0] and lo < ridge.corridor(la + f)[1]
            for f in (0.0, 0.999)
        )
    ]
    print(f"[{ridge.name}] reading {len(needed)} tiles", flush=True)
    for la, lo in needed:
        r, c = (la - ridge.south) * CELLS, (lo - ridge.west) * CELLS
        z[r:r + CELLS, c:c + CELLS] = read_tile(la, lo)
    return z, outer, lon2d, lat2d


def divide(ridge: Ridge) -> LineString:
    z, outer, lon, lat = read_grid(ridge)
    # A tile can reach beyond the corridor, so test the edges, not what was read.
    # Cells beyond them are all markers, so their elevation doesn't matter.
    markers = np.where(outer > 0, outer, ridge.markers(z, lon, lat))
    surface = np.where(outer > 0, -1000.0, z)
    labels = watershed(surface, markers)
    print(f"[{ridge.name}] {int((labels == SIDE_A).sum()):,} cells on one side, "
          f"{int((labels == SIDE_B).sum()):,} on the other", flush=True)

    # Contours of the side-A mask lie on the border between the two sides.
    contours = find_contours((labels == SIDE_A).astype(np.float32), 0.5)
    lines = [
        LineString(
            np.column_stack([
                ridge.west + (c[:, 1] + 0.5) / CELLS,
                ridge.south + (c[:, 0] + 0.5) / CELLS,
            ])
        )
        for c in contours if len(c) > 1
    ]
    main = max(lines, key=lambda g: g.length)
    print(f"[{ridge.name}] {len(lines)} contours, longest {main.length:.1f}°", flush=True)

    coords = np.asarray(main.coords)
    near = lambda p: int(np.argmin(np.hypot(coords[:, 0] - p[0], coords[:, 1] - p[1])))  # noqa: E731
    a, b = near(ridge.start), near(ridge.end)
    part = coords[a:b + 1] if a <= b else coords[b:a + 1][::-1]
    print(f"[{ridge.name}] from {part[0].round(3).tolist()} to {part[-1].round(3).tolist()}", flush=True)
    return shapely.simplify(LineString(part), SIMPLIFY)


def km(geom: LineString) -> float:
    """Rough length, for the log."""
    c = np.asarray(geom.coords)
    mid = math.radians(float(c[:, 1].mean()))
    return float(np.hypot(np.diff(c[:, 0]) * math.cos(mid), np.diff(c[:, 1])).sum() * 111.2)


def main() -> int:
    features = []
    for ridge in RIDGES:
        line = divide(ridge)
        print(f"[{ridge.name}] {len(line.coords)} vertices, about {km(line):,.0f} km", flush=True)
        coords = [[round(x, COORD_DECIMALS), round(y, COORD_DECIMALS)] for x, y in line.coords]
        features.append({
            "type": "Feature",
            "properties": {"name": ridge.name},
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    with OUTPUT.open("w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, separators=(",", ":"))
        f.write("\n")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
