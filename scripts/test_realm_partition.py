"""Tests for realm/partition.py on small fixtures, no downloads or data needed.

Run with `python scripts/test_realm_partition.py` (no test framework required,
like test_overviews.py). Exit code 0 on success, an assertion error otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import shapely  # noqa: E402
from shapely.geometry import Point, Polygon, box  # noqa: E402

from realm.partition import as_coverage, partition_problems  # noqa: E402

GRID = 1e-6
WEST = box(-180, -90, 0, 90)
EAST = box(0, -90, 180, 90)


def test_neighbours_share_their_border_vertex_for_vertex() -> None:
    # west's border has a vertex at (0, 10) that east's lacks
    west = Polygon([(-180, -90), (0, -90), (0, 10), (0, 90), (-180, 90)])
    assert not shapely.coverage_is_valid([west, EAST])
    out, _ = as_coverage({"west": west, "east": EAST}, GRID)
    assert shapely.coverage_is_valid(list(out.values()))
    assert partition_problems(out) == []


def test_slivers_go_to_the_realm_they_border_most() -> None:
    notch = box(0, 0, 0.01, 1)  # in neither realm
    tab = box(0, 10, 0.01, 11)  # in both
    out, _ = as_coverage({"west": WEST.union(tab), "east": EAST.difference(notch)}, GRID)
    # both border west along 1°, and east along 1.02°
    assert out["east"].contains(Point(0.005, 0.5)), "the gap"
    assert out["east"].contains(Point(0.005, 10.5)), "the overlap"
    assert not out["west"].intersects(Point(0.005, 10.5))
    assert partition_problems(out) == []


def test_gaps_on_the_edge_of_the_world_are_filled() -> None:
    # nothing encloses a gap along the antimeridian
    out, _ = as_coverage({"west": box(-179.99, -90, 0, 90), "east": EAST}, GRID)
    assert out["west"].contains(Point(-179.995, 0))
    assert partition_problems(out) == []


def test_vertices_are_on_the_grid() -> None:
    west = box(-180, -90, 0.12345678, 90)
    east = box(0.12345678, -90, 180, 90)
    out, _ = as_coverage({"west": west, "east": east}, GRID)
    xy = np.concatenate([shapely.get_coordinates(g) for g in out.values()])
    assert (xy == np.round(xy, 6)).all()
    assert 0.123457 in xy[:, 0]


def test_a_partition_has_no_problems() -> None:
    assert partition_problems({"west": WEST, "east": EAST}) == []


def test_problems_name_a_gap() -> None:
    [problem] = partition_problems({"west": box(-180, -90, -1, 90), "east": EAST})
    assert "180.000000 sq°" in problem and "no realm" in problem, problem


def test_problems_name_an_overlap() -> None:
    problems = partition_problems({"west": box(-180, -90, 1, 90), "east": EAST})
    assert any(p.startswith("west:") for p in problems), problems
    assert any(p.startswith("east:") for p in problems), problems


def test_problems_name_a_realm_inside_another() -> None:
    problems = partition_problems({"world": box(-180, -90, 180, 90), "island": box(0, 0, 1, 1)})
    assert problems, "an enclave overlapping its surroundings"


def test_problems_name_borders_that_do_not_match() -> None:
    west = Polygon([(-180, -90), (0, -90), (0, 10), (0, 90), (-180, 90)])
    problems = partition_problems({"west": west, "east": EAST})
    assert any(p.startswith("east:") for p in problems), problems


def test_problems_name_an_invalid_realm() -> None:
    bowtie = Polygon([(-180, -90), (0, 90), (0, -90), (-180, 90)])
    problems = partition_problems({"west": bowtie, "east": EAST})
    assert any(p.startswith("west:") and "valid" in p for p in problems), problems


def main() -> int:
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_")]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
