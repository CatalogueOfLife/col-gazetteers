"""Build-wide configuration: target CRS, simplification, paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES_DIR = REPO_ROOT / "sources"
WORK_DIR = REPO_ROOT / "work"

_SUPPORTED_CRS = {"4326", "3857"}

_DEFAULTS = {
    "4326": {
        # 0.005° (~550 m at the equator). World-view-first: imperceptible
        # degradation at z2-z6 zoom levels, halves the on-disk tree compared
        # to 0.001°. See README.
        "simplify_tolerance": 0.005,
        "coord_precision": 6,
    },
    "3857": {
        "simplify_tolerance": 500.0,  # metres, ~equivalent to 0.005° at equator
        "coord_precision": 0,
    },
}


@dataclass(frozen=True)
class Config:
    crs: str  # "4326" or "3857"
    simplify_tolerance: float
    coord_precision: int

    @property
    def epsg(self) -> str:
        return f"EPSG:{self.crs}"


def load_config(
    cli_crs: str | None = None,
    cli_simplify: float | None = None,
) -> Config:
    """Resolve target CRS + simplify tolerance from CLI > env > defaults."""
    crs = cli_crs or os.environ.get("GAZETTEER_CRS", "4326")
    if crs not in _SUPPORTED_CRS:
        raise ValueError(
            f"Unsupported CRS {crs!r}; expected one of {sorted(_SUPPORTED_CRS)}"
        )
    defaults = _DEFAULTS[crs]
    env_simplify = os.environ.get("GAZETTEER_SIMPLIFY")
    simplify = (
        cli_simplify
        if cli_simplify is not None
        else (float(env_simplify) if env_simplify else defaults["simplify_tolerance"])
    )
    return Config(
        crs=crs,
        simplify_tolerance=simplify,
        coord_precision=defaults["coord_precision"],
    )
