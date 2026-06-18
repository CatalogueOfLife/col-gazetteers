"""Write labels.tsv — UTF-8, no header, tab-delimited <id>\\t<name>[\\t<extra>…].

Column 1 is always the canonical English label (what the backend's
`AreaLabelLookup` reads). Extra columns, when present, are gazetteer-specific
(e.g. FAO carries NAME_FR and NAME_ES). Consumers that only need the English
name should split on the first tab only.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


def write_labels(path: Path, rows: Sequence[Sequence[str]]) -> int:
    """Write `rows` to `path`. Sorted by id for stable diffs.

    Each row is `(id, name, *extras)`. Rows may have different arities only
    when the trailing extras are optional; the file's column count is taken
    from the widest row.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sorted(rows, key=lambda r: r[0])
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows_sorted:
            if len(row) < 2:
                raise ValueError(f"row needs at least (id, name): {row!r}")
            area_id = row[0]
            if "\t" in area_id or "\n" in area_id:
                raise ValueError(f"id contains tab/newline: {area_id!r}")
            cells = [_clean(c) for c in row[1:]]
            f.write(area_id + "\t" + "\t".join(cells) + "\n")
    return len(rows_sorted)


def _clean(cell: str) -> str:
    s = "" if cell is None else str(cell)
    # Neutralize every tab/newline flavor (incl. bare \r) so a label can't
    # split a TSV row when read back line-by-line.
    for ch in ("\t", "\r\n", "\r", "\n"):
        s = s.replace(ch, " ")
    return s.strip()
