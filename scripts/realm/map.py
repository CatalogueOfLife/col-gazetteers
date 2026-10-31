"""Render the maps of the 19 `realm` areas. Called by build_maps.py.

realm/map.{svg,png} name every realm on the map, because several colours are too
close to match against a legend alone. realm/map-unlabelled.{svg,png} have only
the legend. Colours are the fixed realm fills of realm/overview.geojson
(REALM_FILLS in build_overviews.py).

In the SVGs every realm, its name and its legend entry carry the id of the realm,
which is what makes realm/map.svg clickable in ChecklistBank.
"""

from __future__ import annotations

import sys
from pathlib import Path

from matplotlib.colors import to_rgb
from matplotlib.patches import Patch
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_maps as bm  # noqa: E402
from common.config import REPO_ROOT  # noqa: E402

# The legend's terrestrial columns, west to east, each north to south.
LAND_COLUMNS = [
    ["Nearctic", "Neotropic"],
    ["Palearctic", "Afrotropic", "Antarctic"],
    ["Indomalaya", "Australasia", "Oceania"],
]
# The legend's marine columns: each realm under its main ocean, north to south.
OCEANS = {
    "Atlantic": ["Boreoatlantic", "Atlantotropic"],
    "Pacific": ["Boreopacific", "Mesopacific", "Panamic", "Magellanic", "Tasmanic"],
    "Indian": ["Indopacific", "Agulhan"],
    "Polar": ["Arctic", "SouthernOcean"],
}
# Where each realm's name goes on the map (lon, lat), and its size if not the
# default. Placed by hand: a centroid can fall on land or in another realm.
# The render fails if a name doesn't fit inside its realm.
SEA_LABELS = {
    "Arctic": (-160, 76, {"fontsize": 10}),
    "Boreoatlantic": (-34, 42, {}),
    "Boreopacific": (-158, 49, {"fontsize": 10}),
    "Atlantotropic": (-25, 2, {}),
    "Panamic": (-112, 2, {}),
    "Mesopacific": (-155, -8, {}),
    "Indopacific": (78, -14, {}),
    "Magellanic": (-108, -32, {}),
    "Agulhan": (40, -33, {"fontsize": 9}),
    "Tasmanic": (128, -40.5, {"fontsize": 10}),
    "SouthernOcean": (0, -60, {}),
}
LAND_LABELS = {
    "Nearctic": (-100, 45.5, {}),
    "Neotropic": (-55, -14, {}),
    "Palearctic": (100, 48, {}),
    "Afrotropic": (29, 5, {"fontsize": 10}),
    "Antarctic": (110, -73.5, {}),
    "Indomalaya": (83, 24.5, {"fontsize": 8}),
    "Australasia": (133, -24.5, {"fontsize": 10}),
}
# Oceania is only islands, too small to hold its name, so the name sits in the
# water beside Hawaii, in Mesopacific, with a line to it.
OCEANIA_LABEL = (-167, 10)
HAWAII = (-155.5, 19.6)
# Share of a name's box that must lie in its realm; islands may be underneath.
LABEL_FIT = 0.97
LAND_INK = "#000000"
SEA_INK = "#ffffff"
# For seas too pale for white ink: still a colour of the sea, not land's black.
PALE_SEA_INK = "#0b2e4f"
MIN_CONTRAST = 3
FIGSIZE = (16, 9.4)


def _luminance(colour: str) -> float:
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in to_rgb(colour)]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def sea_ink(fill: str) -> str:
    white_contrast = (_luminance(SEA_INK) + 0.05) / (_luminance(fill) + 0.05)
    return SEA_INK if white_contrast >= MIN_CONTRAST else PALE_SEA_INK


def draw_labels(ax, names, fills) -> list:
    """Name every realm: upright black capitals on land, italics at sea.

    Returns (realm the name must fit in, text) pairs.
    """
    labels = []
    for rid, (lon, lat, style) in LAND_LABELS.items():
        x, y = bm.project_point(lon, lat)
        labels.append((rid, ax.text(x, y, names[rid].upper(), ha="center", va="center", fontweight="bold",
                                    color=LAND_INK, zorder=6, gid=f"label-{rid}", **{"fontsize": 11, **style})))
    for rid, (lon, lat, style) in SEA_LABELS.items():
        x, y = bm.project_point(lon, lat)
        labels.append((rid, ax.text(x, y, names[rid], ha="center", va="center", style="italic",
                                    color=sea_ink(fills[rid]), zorder=6, gid=f"label-{rid}",
                                    **{"fontsize": 12, **style})))
    (lx, ly), target = bm.project_point(*OCEANIA_LABEL), bm.project_point(*HAWAII)
    labels.append(("Mesopacific", ax.text(lx, ly, names["Oceania"].upper(), ha="center", va="center",
                                          fontsize=9, fontweight="bold", color=LAND_INK, zorder=6, gid="label-Oceania")))
    ax.annotate("", xy=target, xytext=(lx, ly), zorder=6,
                arrowprops={"arrowstyle": "-", "color": LAND_INK, "linewidth": 0.6, "shrinkA": 7, "shrinkB": 2})
    return labels


def check_label_fit(fig, ax, labels, shapes) -> None:
    fig.canvas.draw()
    to_map = ax.transData.inverted()
    too_big = []
    for rid, label in labels:
        (x0, y0), (x1, y1) = to_map.transform(label.get_window_extent())
        area = box(x0, y0, x1, y1)
        share = shapes[rid].intersection(area).area / area.area
        if share < LABEL_FIT:
            too_big.append(f"{label.get_text()} in {rid} {share:.0%}")
    if too_big:
        raise SystemExit(f"names not inside their realm: {too_big}")


def _draw(stem: str, shapes, names, fills, labelled: bool) -> None:
    fig, ax = bm.new_map("19 biogeographic realms covering the globe (Equal Earth projection)", figsize=FIGSIZE)
    for rid, geom in shapes.items():
        bm.draw_area(ax, geom, fills[rid], gid=f"realm-{rid}")

    def legend(ids, title, x):
        entries = fig.legend(handles=[Patch(facecolor=fills[k], edgecolor="#999999", linewidth=0.3, label=names[k]) for k in ids],
                             title=title, loc="upper left", bbox_to_anchor=(x, -0.05), bbox_transform=ax.transAxes,
                             fontsize=10, title_fontsize=10.5, frameon=False, alignment="left", borderaxespad=0, borderpad=0)
        for rid, handle, text in zip(ids, entries.legend_handles, entries.get_texts()):
            handle.set_gid(f"legend-{rid}-colour")
            text.set_gid(f"legend-{rid}-name")
        return entries

    ax.text(0.0, -0.015, "Terrestrial realms", transform=ax.transAxes, fontsize=11.5, fontweight="bold", va="top")
    for ids, x in zip(LAND_COLUMNS, (0.0, 0.1, 0.2)):
        legend(ids, None, x)
    ax.text(0.42, -0.015, "Marine realms", transform=ax.transAxes, fontsize=11.5, fontweight="bold", va="top")
    for (ocean, ids), x in zip(OCEANS.items(), (0.42, 0.57, 0.72, 0.87)):
        legend(ids, ocean, x)

    if labelled:
        check_label_fit(fig, ax, draw_labels(ax, names, fills), shapes)
    for suffix in (".svg", ".png"):
        bm.save(fig, REPO_ROOT / "realm" / f"{stem}{suffix}")
    bm.plt.close(fig)


def render(overview: dict) -> None:
    features = overview["features"]
    fills = {f["properties"]["id"]: f["properties"]["fill"] for f in features}
    names = {f["properties"]["id"]: f["properties"]["name"] for f in features}
    shapes = {f["properties"]["id"]: bm.project(shape(f["geometry"])) for f in features}
    land = {i for ids in LAND_COLUMNS for i in ids}
    sea = {i for ids in OCEANS.values() for i in ids}
    if land | sea != set(fills) or land != {*LAND_LABELS, "Oceania"} or sea != set(SEA_LABELS):
        raise SystemExit(f"realms differ from the legend or labels: {sorted(set(fills) ^ (land | sea))}")
    _draw("map", shapes, names, fills, labelled=True)
    _draw("map-unlabelled", shapes, names, fills, labelled=False)
