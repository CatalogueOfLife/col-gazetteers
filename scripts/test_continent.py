"""Tests for the `continent` gazetteer.

The cut logic is tested on small fixtures. The committed features are then checked
against places whose continent is not in doubt, above all those the physical
boundaries were drawn for.

Run with `python scripts/test_continent.py` (no test framework required, like
test_realm_partition.py). Exit code 0 on success, an assertion error otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shapely  # noqa: E402
from shapely.geometry import LineString, MultiPolygon, Point, box, shape  # noqa: E402
from shapely.geometry.base import BaseGeometry  # noqa: E402

from continent.build import (  # noqa: E402
    LABELS,
    SUBUNIT_CONTINENTS,
    assign_subunit,
    closed_polygon,
    land_crossed,
    share_borders,
    split_by_parts,
    stitch,
)

ROOT = Path(__file__).resolve().parent.parent
GRID = 1e-6
# How far a small island's point may lie from its simplified polygon, in degrees.
NEAR = 0.1

PLACES = {
    "Europe": {
        "Istanbul, Fatih": (28.95, 41.01), "Perm": (56.25, 58.01), "Oral": (51.37, 51.23),
        "Krasnodar": (38.97, 45.04), "Grozny": (45.69, 43.32), "Derbent": (48.28, 42.06),
        "Reykjavik": (-21.9, 64.13), "Ponta Delgada": (-25.67, 37.75), "Novaya Zemlya": (55.0, 72.5),
        "Stepantsminda": (44.65, 42.66),
    },
    "Asia": {
        "Istanbul, Kadıköy": (29.03, 40.99), "Yekaterinburg": (60.6, 56.84), "Aktobe": (57.2, 50.28),
        "Sochi": (39.75, 43.6), "Tbilisi": (44.79, 41.7), "Baku": (49.87, 40.42),
        "Sharm el-Sheikh": (34.33, 27.92), "Nicosia": (33.37, 35.17), "Ambon": (128.18, -3.66),
        "Dili": (125.57, -8.57), "Sulawesi": (120.5, -2.0),
    },
    "Africa": {
        "Cairo": (31.24, 30.04), "Ismailia": (32.25, 30.6), "Las Palmas": (-15.42, 28.1),
        "Socotra": (53.9, 12.5), "Port Louis": (57.5, -20.17),
    },
    "Oceania": {
        "Jayapura": (140.7, -2.55), "Manokwari": (134.05, -0.87), "Aru Islands": (134.4, -6.0),
        "Honolulu": (-157.85, 21.33), "Sydney": (151.2, -33.87),
    },
    "NorthAmerica": {
        "Panama City": (-79.52, 9.0), "Bridgetown": (-59.6, 13.1), "Nuuk": (-51.7, 64.2),
        "Willemstad": (-68.93, 12.13),
    },
    "SouthAmerica": {"Bogotá": (-74.08, 4.62), "Port of Spain": (-61.5, 10.68), "Quito": (-78.47, -0.2)},
    "Antarctica": {"McMurdo": (166.67, -77.85), "Grytviken": (-36.5, -54.28), "South Pole": (0.0, -89.0)},
}


def test_river_segments_join_in_order_and_direction() -> None:
    upper = LineString([(0, 2), (0, 1)])
    middle = LineString([(0, 0), (0, 1)])  # drawn the other way
    lower = LineString([(0, 0), (0, -1)])
    line = stitch([lower, upper, middle], (0, 2))
    assert line.coords[0] == (0, 2) and line.coords[-1] == (0, -1)


def test_stitch_cuts_segments_that_overshoot_each_other() -> None:
    tail = LineString([(0, 2), (0, 0)])
    head = LineString([(-1, 0.5), (1, 0.5), (2, -1)])
    line = stitch([tail, head], (0, 2))
    assert line.is_simple
    assert line.coords[1] == (0, 0.5)


def test_stitch_needs_something() -> None:
    try:
        stitch([], (0, 0))
    except ValueError:
        return
    raise AssertionError("expected a ValueError")


def test_a_boundary_that_crosses_itself_is_refused() -> None:
    try:
        closed_polygon([(0, 0), (1, 1)], [(1, 0), (0, 1)])
    except ValueError:
        return
    raise AssertionError("expected a ValueError")


def test_sea_lines_measure_the_land_they_cross() -> None:
    assert land_crossed([(0, 0), (4, 0)], box(1, -1, 3, 1)) == 2
    assert land_crossed([(0, 5), (4, 5)], box(1, -1, 3, 1)) == 0


def test_turkey_style_parts_stay_whole() -> None:
    side = box(-10, -10, 0, 10)
    west, east = box(-3, 0, -1, 1), box(1, 0, 3, 1)
    inside, outside = split_by_parts(MultiPolygon([west, east]), side)
    assert [p.area for p in inside] == [west.area]
    assert [p.area for p in outside] == [east.area]


def test_overrides_win_and_seven_seas_never_passes_unmapped() -> None:
    assert assign_subunit("Trinidad", "North America") == "SouthAmerica"
    assert assign_subunit("Azores", "Seven seas (open ocean)") == "Europe"
    assert assign_subunit("Panama", "North America") == "NorthAmerica"
    for bad in ("Seven seas (open ocean)", "Atlantis"):
        try:
            assign_subunit("Nowhere", bad)
        except ValueError:
            continue
        raise AssertionError(f"expected a ValueError for {bad}")
    assert set(SUBUNIT_CONTINENTS.values()) <= set(LABELS)


def test_neighbours_share_a_border_and_the_sea_stays_empty() -> None:
    west, east = box(0, 0, 1, 1), box(1, 0, 2, 1)
    snapped, slivers = share_borders({"W": west, "E": east}, GRID)
    assert slivers == 0
    assert shapely.coverage_is_valid([snapped["W"], snapped["E"]])
    assert snapped["W"].area == west.area and snapped["E"].area == east.area
    # Two continents around a bay: the bay is in neither.
    ring = box(0, 0, 3, 3).difference(box(1, 1, 2, 2))
    kept, _ = share_borders({"R": ring}, GRID)
    assert not kept["R"].contains(Point(1.5, 1.5))


def _continents() -> dict[str, BaseGeometry]:
    out = {}
    for cid in LABELS:
        with (ROOT / "continent" / "features" / f"{cid}.geojson").open(encoding="utf-8") as f:
            out[cid] = shape(json.load(f)["geometry"])
    return out


def test_places_are_on_their_continent_and_no_other() -> None:
    continents = _continents()
    wrong = []
    for expected, places in PLACES.items():
        for name, (lon, lat) in places.items():
            point = Point(lon, lat)
            inside = [cid for cid, geom in continents.items() if geom.contains(point)]
            if inside == [expected]:
                continue
            # A coastal town or islet may fall just off the simplified coastline.
            nearest = min(continents, key=lambda cid: continents[cid].distance(point))
            if inside or nearest != expected or continents[expected].distance(point) > NEAR:
                wrong.append(f"{name} ({lon}, {lat}) is in {inside or nearest}, not {expected}")
    assert not wrong, "\n  ".join(wrong)


def test_continents_share_borders_without_overlapping() -> None:
    continents = _continents()
    assert all(geom.is_valid for geom in continents.values())
    assert shapely.coverage_is_valid(list(continents.values()))


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok {test.__name__}")
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
