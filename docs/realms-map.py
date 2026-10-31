# /// script
# requires-python = ">=3.11"
# dependencies = ["geopandas", "matplotlib", "pyogrio"]
# ///
"""Render two maps of the 19 `realm` features: docs/realms.png has only a legend,
docs/realms-labelled.png also names every realm on the map.

Colours are the fixed realm fills of `realm/overview.geojson` (REALM_FILLS in
scripts/build_overviews.py), so run from the repo root after building `realm`
and its overview:

    uv run docs/realms-map.py     # → docs/realms.png, docs/realms-labelled.png
    uv run docs/realms-map.py svg # → the same as SVG, e.g. for Wikimedia Commons

PNGs draw the full `realm/features`. Vector formats draw the simplified shapes of
`realm/overview.geojson` instead, or an SVG would take 18 MB. SVG text stays text,
so that it can be edited and translated.
"""

import json
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import shapely
from matplotlib.colors import to_rgb
from shapely.geometry import Point, box, shape

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FORMAT = sys.argv[1] if len(sys.argv) > 1 else "png"

EQUAL_EARTH = "EPSG:8857"
# Keep rebuilt SVGs identical, like the rest of the tree: no random ids, no date below.
plt.rcParams["svg.hashsalt"] = "realms"
plt.rcParams["svg.fonttype"] = "none"

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


def _luminance(colour: str) -> float:
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in to_rgb(colour)]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def sea_ink(fill: str) -> str:
    white_contrast = (_luminance(SEA_INK) + 0.05) / (_luminance(fill) + 0.05)
    return SEA_INK if white_contrast >= MIN_CONTRAST else PALE_SEA_INK


def _projected_point(lon: float, lat: float) -> Point:
    return gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(EQUAL_EARTH).iloc[0]


def draw_labels(ax, names, fills) -> list:
    """Name every realm: upright black capitals on land, italics at sea.

    Returns (realm the name must fit in, text) pairs.
    """
    labels = []
    for rid, (lon, lat, style) in LAND_LABELS.items():
        point = _projected_point(lon, lat)
        labels.append((rid, ax.text(point.x, point.y, names[rid].upper(), ha="center", va="center", fontweight="bold",
                                    color=LAND_INK, **{"fontsize": 11, **style})))
    for rid, (lon, lat, style) in SEA_LABELS.items():
        point = _projected_point(lon, lat)
        labels.append((rid, ax.text(point.x, point.y, names[rid], ha="center", va="center", style="italic",
                                    color=sea_ink(fills[rid]), **{"fontsize": 12, **style})))
    label, target = _projected_point(*OCEANIA_LABEL), _projected_point(*HAWAII)
    labels.append(("Mesopacific", ax.text(label.x, label.y, names["Oceania"].upper(), ha="center", va="center",
                                          fontsize=9, fontweight="bold", color=LAND_INK)))
    ax.annotate("", xy=(target.x, target.y), xytext=(label.x, label.y),
                arrowprops={"arrowstyle": "-", "color": LAND_INK, "linewidth": 0.6, "shrinkA": 7, "shrinkB": 2})
    return labels


def check_label_fit(fig, ax, labels, projected) -> None:
    fig.canvas.draw()
    shapes = dict(zip(projected["id"], projected["geometry"]))
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


def render(out: Path, projected, sphere, names, fills, labelled: bool) -> None:
    fig, ax = plt.subplots(figsize=(16, 9.4))
    # Outlined in their own colour: without an outline, anti-aliasing leaves hairlines
    # between neighbours, and white ones pile up into noise along fjord coasts.
    projected.plot(ax=ax, color=projected["color"], edgecolor=projected["color"], linewidth=0.3)
    sphere.plot(ax=ax, color="#555555", linewidth=0.8)
    ax.set_axis_off()

    def legend(ids, title, x):
        return fig.legend(handles=[Patch(facecolor=fills[k], edgecolor="#999999", linewidth=0.3, label=names[k]) for k in ids],
                          title=title, loc="upper left", bbox_to_anchor=(x, -0.05), bbox_transform=ax.transAxes,
                          fontsize=10, title_fontsize=10.5, frameon=False, alignment="left", borderaxespad=0, borderpad=0)

    ax.text(0.0, -0.015, "Terrestrial realms", transform=ax.transAxes, fontsize=11.5, fontweight="bold", va="top")
    for ids, x in zip(LAND_COLUMNS, (0.0, 0.1, 0.2)):
        legend(ids, None, x)
    ax.text(0.42, -0.015, "Marine realms", transform=ax.transAxes, fontsize=11.5, fontweight="bold", va="top")
    for (ocean, ids), x in zip(OCEANS.items(), (0.42, 0.57, 0.72, 0.87)):
        legend(ids, ocean, x)

    if labelled:
        check_label_fit(fig, ax, draw_labels(ax, names, fills), projected)

    ax.set_title("19 biogeographic realms covering the globe (Equal Earth projection)", fontsize=14)
    metadata = {"Date": None} if FORMAT == "svg" else None
    fig.savefig(out, dpi=110, bbox_inches="tight", facecolor="white", metadata=metadata)
    plt.close(fig)
    print(f"wrote {out}")


def main() -> None:
    overview = json.loads((ROOT / "realm" / "overview.geojson").read_text())
    fills = {f["properties"]["id"]: f["properties"]["fill"] for f in overview["features"]}
    simplified = {f["properties"]["id"]: f["geometry"] for f in overview["features"]}
    rows = []
    for path in sorted((ROOT / "realm" / "features").glob("*.geojson")):
        feat = json.loads(path.read_text())
        rid = feat["properties"]["realm"]
        # Densify so straight lon/lat edges (the antimeridian, polar caps)
        # follow the projection's curves instead of cutting across them.
        rows.append({"id": rid, "name": feat["properties"]["name"],
                     "marine": "costello" in feat["properties"],
                     "geometry": shapely.segmentize(shape(feat["geometry"] if FORMAT == "png" else simplified[rid]), 1.0)})
    realms = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    missing = set(realms["id"]) ^ set(fills)
    if missing:
        raise SystemExit(f"realm ids without a fill, or fills without a realm: {sorted(missing)}")
    realms["color"] = [fills[i] for i in realms["id"]]
    names = dict(zip(realms["id"], realms["name"]))
    land = {i for i, marine in zip(realms["id"], realms["marine"]) if not marine}
    if land != {i for ids in LAND_COLUMNS for i in ids} or land != {*LAND_LABELS, "Oceania"}:
        raise SystemExit(f"terrestrial realms, LAND_COLUMNS and LAND_LABELS differ: {sorted(land)}")
    sea = {i for i, marine in zip(realms["id"], realms["marine"]) if marine}
    in_legend = {i for ids in OCEANS.values() for i in ids}
    if sea != in_legend or sea != set(SEA_LABELS):
        raise SystemExit(f"marine realms, OCEANS and SEA_LABELS differ: {sorted(sea ^ in_legend | sea ^ set(SEA_LABELS))}")
    sphere = gpd.GeoSeries([shapely.segmentize(box(-180, -90, 180, 90), 1.0)], crs="EPSG:4326").to_crs(EQUAL_EARTH).boundary
    projected = realms.to_crs(EQUAL_EARTH)

    docs = ROOT / "docs"
    render(docs / f"realms.{FORMAT}", projected, sphere, names, fills, labelled=False)
    render(docs / f"realms-labelled.{FORMAT}", projected, sphere, names, fills, labelled=True)


if __name__ == "__main__":
    main()
