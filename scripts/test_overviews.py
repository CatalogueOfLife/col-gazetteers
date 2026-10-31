"""Tests for build_overviews.py on small fixtures, no downloads or data needed.

Run with `python scripts/test_overviews.py` (no test framework required, like
test_id_patterns.py). Exit code 0 on success, an assertion error otherwise.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box, shape  # noqa: E402

import build_overviews as bo  # noqa: E402
from realm.partition import partition_problems  # noqa: E402


def test_id_filters() -> None:
    iso = bo.AREA_OVERVIEWS["iso"].include
    assert iso("DE") and not iso("DE-BE")
    assert not iso("YU") and not iso("AN") and not iso("TP"), "historic codes overlap countries"

    tdwg = bo.AREA_OVERVIEWS["tdwg"].include
    assert tdwg("1") and not tdwg("10") and not tdwg("ABT") and not tdwg("ABT-OO")

    fao = bo.AREA_OVERVIEWS["fao"].include
    assert fao("27") and not fao("27.1") and not fao("47.A.0")

    iho = bo.AREA_OVERVIEWS["iho"].include
    assert iho("1") and iho("28A") and iho("14A")
    assert not iho("1a") and not iho("28a"), "lowercase suffixes are sub-areas"

    assert bo.AREA_OVERVIEWS["teow"].include("847")


def test_neighbours_get_different_colours() -> None:
    # a 5 x 5 grid of cells, where diagonal cells count as neighbours too
    cells = [box(x, y, x + 1, y + 1) for x in range(5) for y in range(5)]
    neighbours = bo.find_neighbours(cells)
    colors = bo.assign_colors(neighbours)
    assert neighbours[0] == {1, 5, 6}
    for i, others in enumerate(neighbours):
        for j in others:
            assert colors[i] != colors[j], f"cells {i} and {j} share colour {colors[i]}"


def test_distant_areas_are_not_neighbours() -> None:
    assert bo.find_neighbours([box(0, 0, 1, 1), box(50, 50, 51, 51)]) == [set(), set()]


def test_neighbours_across_the_antimeridian() -> None:
    east, west = box(179, 0, 180, 1), box(-180, 0, -179, 1)
    assert bo.find_neighbours([east, west]) == [{1}, {0}]
    assert bo.find_neighbours([box(170, 0, 171, 1), west]) == [set(), set()]


def test_palette_exhaustion_fails_the_build() -> None:
    size = bo.MAX_COLORS + 1
    complete = [set(range(size)) - {i} for i in range(size)]
    try:
        bo.assign_colors(complete)
    except ValueError:
        return
    raise AssertionError("expected a failure when the palette is exhausted")


def test_tiny_parts_are_dropped_but_the_largest_stays() -> None:
    mainland_and_rock = box(0, 0, 10, 10).union(box(20, 20, 20.001, 20.001))
    assert bo.simplify_area(mainland_and_rock, 0.01).geom_type == "Polygon"
    tiny = box(0, 0, 0.001, 0.001)
    assert not bo.simplify_area(tiny, 0.01).is_empty


def test_tiny_holes_are_filled_and_large_ones_stay() -> None:
    tiny_hole, large_hole = box(1, 1, 1.001, 1.001), box(5, 5, 7, 7)
    polygon = box(0, 0, 10, 10).difference(tiny_hole).difference(large_hole)
    assert len(bo.simplify_area(polygon, 0.0001).interiors) == 1


def _areas(overview: dict) -> dict:
    return {f["properties"]["id"]: shape(f["geometry"]) for f in overview["features"]}


def test_a_partition_keeps_sharing_its_borders() -> None:
    # a wiggly island, which simplified on its own overlaps the sea and leaves gaps
    radii = [(20 + 2 * math.sin(5 * a) + math.sin(13 * a), a) for a in (2 * math.pi * i / 90 for i in range(90))]
    island = Polygon([(r * math.cos(a), r * math.sin(a)) for r, a in radii])
    rows = [("island", "Island", island), ("sea", "Sea", box(-180, -90, 180, 90).difference(island))]
    overview = bo.build_area_overview(rows, bo.AreaOverview(1.0, partition=True))
    assert partition_problems(_areas(overview)) == []


def test_a_partition_gives_tiny_islands_to_their_surroundings() -> None:
    islet = box(10, 10, 10.05, 10.05)
    rows = [("east", "East", box(0, -90, 180, 90).difference(islet)), ("west", "West", box(-180, -90, 0, 90).union(islet))]
    areas = _areas(bo.build_area_overview(rows, bo.AreaOverview(0.01, partition=True)))
    assert areas["east"].contains(Point(10.025, 10.025))
    assert areas["west"].geom_type == "Polygon"
    assert partition_problems(areas) == []


def test_fixed_fills_are_written() -> None:
    rows = [("1", "One", box(0, 0, 1, 1)), ("2", "Two", box(1, 0, 2, 1))]
    overview = bo.build_area_overview(rows, bo.AreaOverview(0.01, fills={"1": "#0f8554", "2": "#cc503e"}))
    assert [f["properties"]["fill"] for f in overview["features"]] == ["#0f8554", "#cc503e"]
    assert set(overview["features"][0]["properties"]) == {"id", "name", "color", "fill"}


def test_a_missing_fill_fails_the_build() -> None:
    rows = [("1", "One", box(0, 0, 1, 1)), ("2", "Two", box(1, 0, 2, 1))]
    try:
        bo.build_area_overview(rows, bo.AreaOverview(0.01, fills={"1": "#0f8554"}))
    except ValueError:
        return
    raise AssertionError("expected a failure when an area has no fill")


def test_every_realm_has_its_own_fill() -> None:
    realm = bo.AREA_OVERVIEWS["realm"]
    ids = {area_id for area_id, _, _ in bo.read_labels(bo.REPO_ROOT / "realm")}
    assert realm.partition and realm.fills is not None
    assert set(realm.fills) == ids
    assert len(set(realm.fills.values())) == len(ids)


def test_coverage_marks_the_cells_of_points_lines_and_polygons() -> None:
    rows = [
        ("", Point(10.1, 10.1)),
        ("", Point(10.2, 10.2)),  # the same cell as the first
        ("", LineString([(30.1, 30.1), (31.4, 30.1)])),
        ("", box(50.1, 50.1, 51.4, 51.4)),
    ]
    [feature] = bo.build_coverage_overview(rows)["features"]
    assert feature["properties"] == {"coverage": True, "count": 4, "omitted": 0}
    assert feature["geometry"]["type"] == "MultiPolygon"
    bounds = sorted(MultiPolygon(shape(feature["geometry"])).geoms, key=lambda p: p.bounds)
    assert [p.bounds for p in bounds] == [(10, 10, 10.5, 10.5), (30, 30, 31.5, 30.5), (50, 50, 51.5, 51.5)]


def test_coverage_stays_inside_the_world() -> None:
    rows = [("", Point(180, 0)), ("", Point(200, 0))]
    [feature] = bo.build_coverage_overview(rows)["features"]
    assert shape(feature["geometry"]).bounds == (179.5, -0.5, 180, 0.5)


def test_coverage_omits_large_entries_and_excluded_types() -> None:
    config = bo.CoverageOverview(max_area=10, exclude_types=frozenset({"grid"}))
    rows = [("", box(-180, -90, 180, 90)), ("grid", Point(50.1, 50.1)), ("", Point(0.1, 0.1))]
    [feature] = bo.build_coverage_overview(rows, config)["features"]
    assert feature["properties"] == {"coverage": True, "count": 1, "omitted": 2}
    assert shape(feature["geometry"]).bounds == (0, 0, 0.5, 0.5)


def test_coverage_fails_without_any_entry() -> None:
    try:
        bo.build_coverage_overview([])
    except ValueError:
        return
    raise AssertionError("expected a failure when nothing is covered")


def test_build_prefix_is_deterministic() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        features = root / "teow" / "features"
        features.mkdir(parents=True)
        (root / "teow" / "labels.tsv").write_text("1\tOne\tExtra\n2\tTwo\n3\tNo feature\n", encoding="utf-8")
        for area_id, x in (("1", 0), ("2", 1)):
            geometry = {"type": "Polygon", "coordinates": [[[x, 0], [x + 1, 0], [x + 1, 1], [x, 1], [x, 0]]]}
            (features / f"{area_id}.geojson").write_text(
                json.dumps({"type": "Feature", "properties": {"name": "ignored"}, "geometry": geometry}), encoding="utf-8"
            )

        bo.build_prefix("teow", root)
        first = (root / "teow" / "overview.geojson").read_bytes()
        bo.build_prefix("teow", root)
        assert first == (root / "teow" / "overview.geojson").read_bytes()

        features_out = json.loads(first)["features"]
        assert [f["properties"]["id"] for f in features_out] == ["1", "2"]
        assert features_out[0]["properties"]["name"] == "One"
        assert set(features_out[0]["properties"]) == {"id", "name", "color"}
        assert features_out[0]["properties"]["color"] != features_out[1]["properties"]["color"]


def main() -> int:
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_")]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
