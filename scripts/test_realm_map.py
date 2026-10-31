"""Every realm of the labelled map has to stay clickable, whatever realms get renamed to.

Run with `python scripts/test_realm_map.py` (no test framework required, like
test_id_patterns.py). Exit code 0 on success, an assertion error otherwise.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_map_has_every_realm() -> None:
    svg = (ROOT / "realm" / "map.svg").read_text()
    realms = {line.split("\t")[0] for line in (ROOT / "realm" / "labels.tsv").read_text().splitlines()}
    for kind in ("realm", "label", "legend"):
        found = set(re.findall(rf'<g id="{kind}-([A-Za-z]+)', svg))
        assert found == realms, f"{kind} ids differ from the labels: {sorted(found ^ realms)}"


if __name__ == "__main__":
    test_map_has_every_realm()
    print("OK test_map_has_every_realm")
