"""Write labels.tsv — UTF-8, no header, tab-delimited <id>\\t<name>."""

from __future__ import annotations

from pathlib import Path


def write_labels(path: Path, rows: list[tuple[str, str]]) -> int:
    """Write `rows` to `path`. Sorted by id for stable diffs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sorted(rows, key=lambda r: r[0])
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for area_id, name in rows_sorted:
            if "\t" in area_id or "\n" in area_id:
                raise ValueError(f"id contains tab/newline: {area_id!r}")
            clean_name = name.replace("\t", " ").replace("\n", " ").strip()
            f.write(f"{area_id}\t{clean_name}\n")
    return len(rows_sorted)
