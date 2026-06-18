"""Per-gazetteer build manifest (build.json)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .download import SourceRecord


def _git_short_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _ogr2ogr_version() -> str:
    try:
        out = subprocess.check_output(
            ["ogr2ogr", "--version"], stderr=subprocess.DEVNULL, text=True
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def write_manifest(
    path: Path,
    *,
    prefix: str,
    sources: list[SourceRecord],
    feature_count: int,
    label_count: int,
    config: Config | None = None,
) -> None:
    """Write a gazetteer's build.json.

    Pass `config` for geometry builds (records the target `crs` and
    `simplify_tolerance`). Labels-only gazetteers (no geometries, e.g. `wdpa`)
    omit it; the geometry-specific fields and the ogr2ogr tool version are then
    left out.
    """
    geometry_build = config is not None
    manifest: dict = {
        "prefix": prefix,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if geometry_build:
        manifest["crs"] = config.epsg
        manifest["simplify_tolerance"] = config.simplify_tolerance
    manifest["feature_count"] = feature_count
    manifest["label_count"] = label_count
    manifest["sources"] = [s.to_json() for s in sources]
    tools = {
        "python": sys.version.split()[0],
        "build_script_commit": _git_short_sha(),
    }
    if geometry_build:
        tools = {"ogr2ogr": _ogr2ogr_version(), **tools}
    manifest["tools"] = tools
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
