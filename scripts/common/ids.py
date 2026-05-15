"""Filename normalization for features/<id>.geojson."""

from __future__ import annotations

import re

_BAD = re.compile(r"[\s:/\\]+")
_REPEAT_DASH = re.compile(r"-{2,}")


def normalize_id(raw: object) -> str:
    """Strip path-dangerous characters; **case is preserved**.

    Some upstream datasets (e.g. IHO S-23 sub-basin codes like `28A` vs `28a`)
    use case as a meaningful distinction between parent and child areas.
    Lowercasing would collide them. We only collapse whitespace/colons/slashes
    into dashes and forbid empty / dot-traversal results.

    Note: the resulting filenames are case-sensitive, so the on-disk tree must
    live on a case-sensitive filesystem (APFS-cs on macOS, ext4/xfs on Linux).
    Default macOS HFS+/APFS-ci will silently merge `28A.geojson` and
    `28a.geojson` at checkout time.
    """
    s = str(raw).strip()
    s = _BAD.sub("-", s)
    s = _REPEAT_DASH.sub("-", s).strip("-")
    if not s or s in {".", ".."}:
        raise ValueError(f"id normalizes to empty/forbidden: {raw!r}")
    return s


def assert_unique(ids: list[str]) -> None:
    seen: dict[str, int] = {}
    for i in ids:
        seen[i] = seen.get(i, 0) + 1
    dupes = sorted(k for k, v in seen.items() if v > 1)
    if dupes:
        raise ValueError(f"duplicate ids after normalization: {dupes[:10]}")
