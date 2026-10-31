"""Check that every gazetteer has its description, and that it holds together.

Each gazetteer needs a README.md with the agreed sections, a map linked from its
introduction, and a link from the main README. Relative links must resolve, so that
moving a file can't leave a description pointing at nothing.

Run with `python scripts/test_descriptions.py` (no test framework required).
Exit code 0 on success, an assertion error otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Prefixes without geometries have no map, and say why instead.
NO_MAP = {"wdpa"}
SECTIONS = ("## Sources and attribution", "## How the shapes were made", "## Identifiers and names",
            "## Technical details", "## References")
LINK = re.compile(r"\]\(([^)\s]+)\)")


def gazetteers() -> list[str]:
    return sorted(p.parent.name for p in ROOT.glob("*/labels.tsv"))


def relative_links(text: str) -> list[str]:
    links = [m for m in LINK.findall(text) if "://" not in m and not m.startswith(("#", "mailto:"))]
    return [m.split("#")[0] for m in links]


def broken_links(path: Path) -> list[str]:
    return [f"{path.relative_to(ROOT)}: {link}" for link in relative_links(path.read_text(encoding="utf-8"))
            if not (path.parent / link).exists()]


def test_every_gazetteer_is_described() -> None:
    for prefix in gazetteers():
        path = ROOT / prefix / "README.md"
        assert path.is_file(), f"{prefix} has no README.md"
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# "), f"{prefix}: no title"
        missing = [s for s in SECTIONS if not re.search(rf"^{re.escape(s)}$", text, re.M)]
        assert not missing, f"{prefix}: missing {missing}"


def test_every_gazetteer_with_geometries_has_its_map() -> None:
    for prefix in gazetteers():
        if prefix in NO_MAP:
            assert not (ROOT / prefix / "features").exists(), f"{prefix} has geometries, so it needs a map"
            continue
        for name in ("map.svg", "map.png"):
            assert (ROOT / prefix / name).is_file(), f"{prefix} has no {name}"
        intro = (ROOT / prefix / "README.md").read_text(encoding="utf-8").split("\n## ")[0]
        assert "(map.png)" in intro and "(map.svg)" in intro, f"{prefix}: the introduction doesn't show the map"


def test_main_readme_links_every_description_and_no_source_column() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for prefix in gazetteers():
        assert f"({prefix}/README.md)" in text, f"README.md doesn't link {prefix}"
    assert "Upstream source" not in text


def test_relative_links_resolve() -> None:
    broken = []
    for path in (ROOT / "README.md", ROOT / "ATTRIBUTIONS.md", *(ROOT / p / "README.md" for p in gazetteers())):
        broken += broken_links(path)
    assert not broken, "\n  ".join(broken)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok {test.__name__}")
    print(f"{len(tests)} tests passed for {len(gazetteers())} gazetteers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
