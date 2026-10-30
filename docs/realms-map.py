# /// script
# requires-python = ">=3.11"
# dependencies = ["geopandas", "matplotlib", "pyogrio"]
# ///
"""Render docs/realms.png, the map of the 19 `realm` features.

Run from the repo root after building `realm`:

    uv run docs/realms-map.py            # → docs/realms.png
    uv run docs/realms-map.py realms.svg # any matplotlib format, e.g. SVG for Wikimedia Commons
"""

import json
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import shapely
from shapely.geometry import box, shape

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "realms.png"

LAND = {  # earth tones
    "Palearctic": "#d9a05b", "Nearctic": "#c97b63", "Afrotropic": "#e8c66f", "Neotropic": "#8fb36a",
    "Indomalaya": "#b5838d", "Australasia": "#e3a587", "Oceania": "#6d9a52", "Antarctic": "#e9e4dc",
}
SEA = {  # blues, teals, violets
    "Arctic": "#dbe9f6", "TemperateNorthernAtlantic": "#6f8fc8", "TemperateNorthernPacific": "#7fb4c9",
    "TropicalAtlantic": "#a9d3e8", "TropicalEasternPacific": "#5aa6a6", "CentralPacific": "#8ec6c0",
    "IndoWestPacific": "#4d7fb3", "TemperateSouthAmerica": "#9b8fc9", "TemperateSouthernAfrica": "#3f6e8c",
    "TemperateAustralasia": "#b4a7d6", "SouthernOcean": "#c6d6e6",
}
EQUAL_EARTH = "EPSG:8857"


def main() -> None:
    rows = []
    for path in sorted((ROOT / "realm" / "features").glob("*.geojson")):
        feat = json.loads(path.read_text())
        rid = feat["properties"]["realm"]
        # Densify so straight lon/lat edges (the antimeridian, polar caps)
        # follow the projection's curves instead of cutting across them.
        rows.append({"id": rid, "name": feat["properties"]["name"],
                     "geometry": shapely.segmentize(shape(feat["geometry"]), 1.0)})
    realms = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    missing = set(realms["id"]) ^ (set(LAND) | set(SEA))
    if missing:
        raise SystemExit(f"realm ids without a colour, or colours without a realm: {sorted(missing)}")
    realms["color"] = [SEA.get(i) or LAND[i] for i in realms["id"]]
    names = dict(zip(realms["id"], realms["name"]))
    sphere = gpd.GeoSeries([shapely.segmentize(box(-180, -90, 180, 90), 1.0)], crs="EPSG:4326")

    fig, ax = plt.subplots(figsize=(16, 9.4))
    projected = realms.to_crs(EQUAL_EARTH)
    projected.plot(ax=ax, color=projected["color"], edgecolor="white", linewidth=0.25)
    sphere.to_crs(EQUAL_EARTH).boundary.plot(ax=ax, color="#555555", linewidth=0.8)
    ax.set_axis_off()

    def handles(colors):
        return [Patch(facecolor=colors[k], edgecolor="#999999", linewidth=0.3, label=names[k])
                for k in sorted(colors, key=lambda k: names[k])]
    land = ax.legend(handles=handles(LAND), title="Terrestrial realms", loc="upper left",
                     bbox_to_anchor=(0.0, -0.01), ncol=4, fontsize=10, title_fontsize=11,
                     frameon=False, alignment="left")
    ax.add_artist(land)
    ax.legend(handles=handles(SEA), title="Marine realms", loc="upper right",
              bbox_to_anchor=(1.0, -0.01), ncol=3, fontsize=10, title_fontsize=11,
              frameon=False, alignment="left")
    ax.set_title("19 biogeographic realms covering the globe (Equal Earth projection)", fontsize=14)
    fig.savefig(OUT, dpi=110, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
