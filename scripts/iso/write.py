"""Writing the iso tree: both shape sets, symlinks, labels.tsv and sources.tsv."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from common.config import Config
from common.labels import write_labels

from .geom import polys
from .model import Countries, FitResult
from .universe import LEGACY_ALIASES

SOURCES_HEADER = ("id", "resolution", "upstream", "target", "fit", "note")
# A shape that kept its polygons and moved by less than this share of its area, or less than
# one simplification cell, is the one already published: another GDAL or CPU moves vertices
# in the last digits, and simplifying can turn that into a different neighbouring vertex.
SAME_SHAPE_SHARE = 1e-5


def settle_labels(labels: Mapping[str, str], floor: Mapping[str, str], fitted: Mapping[str, FitResult]) -> dict[str, str]:
    """New ids still labelled with their own code take their source's own name."""
    out = dict(labels)
    for code, label in labels.items():
        if code not in floor and label == code and code in fitted and fitted[code].candidate.label:
            out[code] = fitted[code].candidate.label
    return out


def _prepared(geom: BaseGeometry, config: Config) -> dict:
    geom = shapely.simplify(geom, config.simplify_tolerance, preserve_topology=True)
    geom = shapely.transform(geom, lambda c: np.round(c, config.coord_precision))
    if not geom.is_valid:
        geom = polys(geom)
    return mapping(geom)


def same_shape(old: BaseGeometry, new: BaseGeometry, *, noise_area: float) -> bool:
    """Whether `new` is `old` up to noise: the same number of polygons, and the area between
    them below SAME_SHAPE_SHARE of theirs or below `noise_area`, whichever is larger."""
    if shapely.get_num_geometries(old) != shapely.get_num_geometries(new):
        return False
    try:
        moved = old.symmetric_difference(new).area
    except shapely.errors.GEOSException:
        return False
    return moved <= max(SAME_SHAPE_SHARE * max(old.area, new.area), noise_area)


def _published(dirs: tuple[Path, ...]) -> dict[Path, str]:
    """The feature files a rewrite replaces, by path; links are written anew anyway."""
    return {p: p.read_text(encoding="utf-8") for d in dirs if d.exists()
            for p in d.glob("*.geojson") if not p.is_symlink()}


def _write_feature(path: Path, code: str, label: str, source: str, geom: BaseGeometry, config: Config,
                   published: Mapping[Path, str]) -> None:
    """Write a feature, or keep the published file when only noise tells them apart."""
    feature = {
        "type": "Feature",
        "properties": {"iso_id": code, "iso_name": label, "name": label, "source": source},
        "geometry": _prepared(geom, config),
    }
    text = json.dumps(feature, ensure_ascii=False, separators=(",", ":")) + "\n"
    old_text = published.get(path)
    if old_text is not None and old_text != text:
        old = json.loads(old_text)
        noise = config.simplify_tolerance ** 2
        if old.get("properties") == feature["properties"] and \
                same_shape(shape(old["geometry"]), shape(feature["geometry"]), noise_area=noise):
            text = old_text
    path.write_text(text, encoding="utf-8")


def _final_target(code: str, aliases: Mapping[str, str]) -> str:
    seen = set()
    while code in aliases and code not in seen:
        seen.add(code)
        code = aliases[code]
    return code


def _alias_kind(code: str, target: str) -> str:
    if code in LEGACY_ALIASES:
        return "alias-legacy"
    return "alias-dual" if len(target) == 2 else "alias-superseded"


def require_degrees(config: Config) -> None:
    """The iso build, its fitting and checks included, works in degrees."""
    if config.crs != "4326":
        raise NotImplementedError(f"the iso build writes EPSG:4326 only, not EPSG:{config.crs} (GAZETTEER_CRS)")


def write_tree(iso_dir: Path, countries: Countries, fitted: Mapping[str, FitResult], labels: Mapping[str, str],
               aliases: Mapping[str, str], config: Config) -> tuple[int, int]:
    """Write features/, eez/features/, labels.tsv and sources.tsv. Returns (features, labels)."""
    require_degrees(config)
    land_dir, eez_dir = iso_dir / "features", iso_dir / "eez" / "features"
    published = _published((land_dir, eez_dir))
    # Only the features: eez/ also holds the overview and maps, which a rebuild redraws only on change.
    for d in (land_dir, eez_dir):
        if d.exists():
            shutil.rmtree(d)
    land_dir.mkdir(parents=True)
    eez_dir.mkdir(parents=True)
    sources = []

    for code in sorted(countries.land):
        if code not in labels:
            continue
        source = "gbif-political" if code not in ("YU", "AN") else "dissolved"
        _write_feature(land_dir / f"{code}.geojson", code, labels[code], source, countries.land[code], config, published)
        _write_feature(eez_dir / f"{code}.geojson", code, labels[code], source, countries.eez[code], config, published)
        sources.append((code, source, "; ".join(countries.rows.get(code, [])), "", "", ""))

    for code in sorted(fitted):
        r = fitted[code]
        _write_feature(land_dir / f"{code}.geojson", code, labels[code], r.candidate.source, r.geom, config, published)
        note = r.candidate.note
        if r.mode == "conflict":
            note = f"conflict: GBIF holds this area in {r.holder or 'no country'}" + (f"; {note}" if note else "")
        sources.append((code, r.candidate.resolution, r.candidate.source, "", r.mode, note))

    for code in sorted(aliases):
        if code not in labels:
            continue
        target = aliases[code]
        os.symlink(f"{target}.geojson", land_dir / f"{code}.geojson")
        if _final_target(code, aliases) in countries.eez:
            os.symlink(f"{target}.geojson", eez_dir / f"{code}.geojson")
        sources.append((code, _alias_kind(code, target), "", target, "", ""))

    written = {row[0] for row in sources}
    sources += [(code, "label-only", "", "", "", "") for code in labels if code not in written]
    with (iso_dir / "sources.tsv").open("w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(SOURCES_HEADER) + "\n")
        for row in sorted(sources):
            f.write("\t".join(cell.replace("\t", " ").replace("\n", " ") for cell in row) + "\n")

    label_count = write_labels(iso_dir / "labels.tsv", [(code, label) for code, label in labels.items()])
    return len(list(land_dir.glob("*.geojson"))), label_count
