"""Render <prefix>/map.svg and <prefix>/map.png, a world map of each gazetteer.

Every map draws <prefix>/overview.geojson in Equal Earth, on the land of
continent/overview.geojson. `wdpa` has no map, as it has no geometries.

Colours come from each feature's `fill`, or else from its palette index, which
differs between neighbours but means nothing else. SVG text stays text, so that
it can be edited and translated. Output is deterministic for a given version of
matplotlib. The rebuild only renders gazetteers whose overview changed, so that a
new matplotlib doesn't commit every map.

Run with `python scripts/build_maps.py [prefix ...]`, after build_overviews.py.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

import matplotlib
import numpy as np
import shapely
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from shapely.geometry import Polygon, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.config import REPO_ROOT  # noqa: E402

# Keep rebuilt SVGs identical: no random ids, no date (see `save`).
plt.rcParams["svg.hashsalt"] = "col-gazetteers"
plt.rcParams["svg.fonttype"] = "none"

FIGSIZE = (16, 8.4)
DPI = 110
OCEAN = "#eaf1f6"
LAND = "#d4d4d0"
LAND_OVER_SEA_AREAS = "#bfbfba"
OUTLINE = "#555555"
COVERAGE = "#008695"
# CARTO Bold (https://carto.com/carto-colors/), for the palette index of an overview.
PALETTE = ["#7f3c8d", "#11a579", "#3969ac", "#f2b701", "#e73f74", "#80ba5a", "#e68310", "#008695"]
# Longest edge when projecting, so straight lines in lon/lat follow the curves.
SEGMENT = 2.0

# Equal Earth (Šavrič, Patterson & Jenny 2019).
_A1, _A2, _A3, _A4 = 1.340264, -0.081106, 0.000893, 0.003796
_M = math.sqrt(3) / 2


def equal_earth(coords: np.ndarray) -> np.ndarray:
    """Project (lon, lat) in degrees to Equal Earth on the unit sphere."""
    lon, lat = np.radians(coords[:, 0]), np.radians(coords[:, 1])
    theta = np.arcsin(_M * np.sin(lat))
    t2 = theta * theta
    t6 = t2 * t2 * t2
    x = lon * np.cos(theta) / (_M * (_A1 + 3 * _A2 * t2 + t6 * (7 * _A3 + 9 * _A4 * t2)))
    y = theta * (_A1 + _A2 * t2 + t6 * (_A3 + _A4 * t2))
    return np.column_stack([x, y])


def project(geom: BaseGeometry) -> BaseGeometry:
    return shapely.transform(shapely.segmentize(geom, SEGMENT), equal_earth)


def project_point(lon: float, lat: float) -> tuple[float, float]:
    x, y = equal_earth(np.array([[lon, lat]]))[0]
    return float(x), float(y)


def _polygons(geom: BaseGeometry) -> Iterable[Polygon]:
    for part in shapely.get_parts(geom):
        if isinstance(part, Polygon):
            yield part
        else:
            yield from _polygons(part)


def to_path(geom: BaseGeometry) -> MplPath:
    """A compound path of projected polygons. Holes wind the other way, so they stay holes."""
    paths = []
    for polygon in _polygons(geom):
        ordered = orient(polygon)
        for ring in (ordered.exterior, *ordered.interiors):
            coords = np.asarray(ring.coords)
            codes = np.full(len(coords), MplPath.LINETO, dtype=MplPath.code_type)
            codes[0], codes[-1] = MplPath.MOVETO, MplPath.CLOSEPOLY
            paths.append(MplPath(coords, codes))
    return MplPath.make_compound_path(*paths)


def draw_area(ax, projected: BaseGeometry, colour: str, zorder: float = 1.0, gid: str | None = None) -> None:
    # Outlined in their own colour: without an outline, anti-aliasing leaves
    # hairlines between neighbours, which pile up into noise along fjord coasts.
    ax.add_patch(PathPatch(to_path(projected), facecolor=colour, edgecolor=colour, linewidth=0.3, zorder=zorder, gid=gid))


def new_map(title: str, *, ocean: str = OCEAN, figsize: tuple[float, float] = FIGSIZE):
    """A figure with the outline of the globe, ready to be drawn on."""
    fig, ax = plt.subplots(figsize=figsize)
    sphere = project(box(-180, -90, 180, 90))
    ax.add_patch(PathPatch(to_path(sphere), facecolor=ocean, edgecolor="none", zorder=0))
    ax.add_patch(PathPatch(to_path(sphere), facecolor="none", edgecolor=OUTLINE, linewidth=0.8, zorder=5))
    minx, miny, maxx, maxy = sphere.bounds
    ax.set_xlim(minx * 1.005, maxx * 1.005)
    ax.set_ylim(miny * 1.01, maxy * 1.01)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(title, fontsize=14)
    return fig, ax


def save(fig, out: Path) -> None:
    """Without the metadata that would change on every run."""
    metadata = {"Date": None} if out.suffix == ".svg" else {"Software": None}
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor="white", metadata=metadata)
    print(f"wrote {out.relative_to(REPO_ROOT)} ({out.stat().st_size / 1e6:.2f} MB)", flush=True)


@dataclass(frozen=True)
class MapConfig:
    title: str
    # Land is drawn over areas that overlap it, and under areas on land.
    land_over: bool = False
    # What to write on each area: its "id" or "name", if anything.
    label: str | None = None
    fontsize: float = 8.0
    # Where a label goes (lon, lat), by id. Else inside the area's largest part.
    label_at: dict[str, tuple[float, float]] = field(default_factory=dict)


MAPS: dict[str, MapConfig] = {
    "continent": MapConfig(
        "The 7 continents", label="name", fontsize=11,
        label_at={
            "Africa": (20, 8), "Antarctica": (20, -80), "Asia": (92, 45), "Europe": (22, 51),
            "NorthAmerica": (-100, 47), "Oceania": (134, -25), "SouthAmerica": (-60, -14),
        },
    ),
    "tdwg": MapConfig(
        "TDWG WGSRPD: the 9 botanical continents of level 1", label="name", fontsize=9,
        # The Pacific is only islands, Antarctica's largest part runs off the map.
        label_at={"6": (-155, -18), "9": (20, -80)},
    ),
    "iso": MapConfig("ISO 3166-1: the countries of the world"),
    "fao": MapConfig("FAO Major Fishing Areas", land_over=True, label="id", fontsize=10),
    "iho": MapConfig("IHO Limits of Oceans and Seas (S-23)", land_over=True),
    "longhurst": MapConfig("Longhurst Biogeographical Provinces", land_over=True, label="id", fontsize=6),
    "costello": MapConfig("Marine biogeographic realms of Costello et al. 2017", land_over=True, label="id", fontsize=9),
    "teow": MapConfig("Terrestrial Ecoregions of the World"),
    "gi": MapConfig("Global Islands: 0.5° cells holding a named island"),
    "mrgid": MapConfig("Marine Regions: 0.5° cells holding a gazetteer entry", land_over=True),
}
# Gazetteers with a renderer of their own: a module with `render(overview)`.
CUSTOM = {"realm": "realm.map"}


def read_overview(prefix: str) -> dict:
    with (REPO_ROOT / prefix / "overview.geojson").open(encoding="utf-8") as f:
        return json.load(f)


def label_point(geom: BaseGeometry) -> tuple[float, float]:
    """Inside the largest part of an unprojected area, which is where a name fits."""
    largest = max(_polygons(geom), key=lambda p: p.area)
    point = largest.representative_point()
    return point.x, point.y


def draw_land(ax, *, zorder: float, colour: str = LAND) -> None:
    for feature in read_overview("continent")["features"]:
        draw_area(ax, project(shape(feature["geometry"])), colour, zorder)


def render(prefix: str, config: MapConfig) -> None:
    fig, ax = new_map(config.title)
    overview = read_overview(prefix)
    if prefix != "continent":
        draw_land(ax, zorder=2 if config.land_over else 0.5, colour=LAND_OVER_SEA_AREAS if config.land_over else LAND)
    for feature in overview["features"]:
        props = feature["properties"]
        geom = shape(feature["geometry"])
        if "fill" in props:
            colour = props["fill"]
        elif "color" in props:
            colour = PALETTE[props["color"]]
        else:
            colour = COVERAGE
        draw_area(ax, project(geom), colour)
        if config.label:
            area_id = props["id"]
            lon, lat = config.label_at.get(area_id) or label_point(geom)
            x, y = project_point(lon, lat)
            ax.text(x, y, props[config.label], ha="center", va="center", fontsize=config.fontsize,
                    fontweight="bold" if config.label == "name" else "normal", zorder=6,
                    bbox={"facecolor": "white", "alpha": 0.65, "edgecolor": "none", "pad": 1.0})
    for suffix in (".svg", ".png"):
        save(fig, REPO_ROOT / prefix / f"map{suffix}")
    plt.close(fig)


def main() -> int:
    known = [*MAPS, *CUSTOM]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prefixes", nargs="*", help=f"default: all of {', '.join(known)}")
    args = parser.parse_args()
    unknown = set(args.prefixes) - set(known)
    if unknown:
        parser.error(f"no map for {', '.join(sorted(unknown))}")
    for prefix in args.prefixes or known:
        if prefix in CUSTOM:
            import_module(CUSTOM[prefix]).render(read_overview(prefix))
        else:
            render(prefix, MAPS[prefix])
    return 0


if __name__ == "__main__":
    sys.exit(main())
