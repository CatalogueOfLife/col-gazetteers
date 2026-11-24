"""Subdivision shapes from geoBoundaries (gbOpen), for ids no other source has.

geoBoundaries carries ISO 3166-2 codes (`shapeISO`) only at the first level, so
older units such as Albania's former districts are matched by name. Names are
matched at district level (ADM2) before ADM1 and never at ADM3, where same-named
towns would match prefectures or districts.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

from shapely.geometry import shape

from common.download import SourceRecord, download

from .geom import polys
from .model import Candidate

API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/{adm}/"
LEVELS = ("ADM2", "ADM1")  # name matching order; ADM3 is never used

_GENERIC = re.compile(
    r"\b(district|county|province|prefecture|region|municipality|canton|governorate|department|"
    r"rrethi|okres|nomos|island|islands|atoll|state|city|of|the)\b"
)


def norm(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", _GENERIC.sub(" ", ascii_name))


def match_layers(wanted: Mapping[str, str], layers: Mapping[str, tuple[list[dict], str]]) -> dict[str, Candidate]:
    """id -> candidate, from one country's layers {adm: (features, licence)}, by shapeISO, else by name."""
    by_iso, by_name = {}, {}
    for adm in LEVELS:
        features, licence = layers.get(adm, ([], ""))
        for feature in features:
            props = feature.get("properties") or {}
            hit = (adm, feature, licence)
            if props.get("shapeISO"):
                by_iso.setdefault(props["shapeISO"], hit)
            by_name.setdefault(norm(props.get("shapeName") or ""), hit)
    out = {}
    for code, label in wanted.items():
        hit = by_iso.get(code) or by_name.get(norm(label))
        if not hit or not hit[1].get("geometry"):
            continue
        adm, feature, licence = hit
        geom = polys(shape(feature["geometry"]))
        if not geom.is_empty:
            out[code] = Candidate(geom, f"geoboundaries-{adm.lower()}", "geoboundaries",
                                  note=f"geoBoundaries {adm}, {licence}",
                                  label=(feature.get("properties") or {}).get("shapeName", ""))
    return out


def _layer(iso3: str, adm: str, target: Path, force: bool) -> tuple[list[dict], str, list[SourceRecord]]:
    try:
        meta_path, meta_record = download(
            API.format(iso3=iso3, adm=adm), target, role=f"geoboundaries-{iso3}-{adm}-meta",
            name=f"geoBoundaries API: {iso3} {adm}", filename=f"{iso3}_{adm}.json", force=force,
            upstream_version="", timeout=120,
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        url = meta.get("simplifiedGeometryGeoJSON")
        if not url:
            return [], "", [meta_record]
        path, record = download(
            url, target, role=f"geoboundaries-{iso3}-{adm}", name=f"geoBoundaries gbOpen: {iso3} {adm}",
            filename=f"{iso3}_{adm}.geojson", force=force, upstream_version=meta.get("boundaryYearRepresented", ""),
            timeout=300,
        )
    except Exception as e:  # most countries have no ADM2, or no layer at all
        print(f"[iso] geoBoundaries {iso3} {adm}: {e}")
        return [], "", []
    features = json.loads(path.read_text(encoding="utf-8")).get("features", [])
    return features, meta.get("boundaryLicense", ""), [meta_record, record]


def geoboundaries_candidates(wanted: Mapping[str, str], sources_dir: Path, *, iso3: Mapping[str, str],
                             force: bool) -> tuple[dict[str, Candidate], list[SourceRecord]]:
    """Candidates for `wanted` (id -> label), fetching only the countries they belong to."""
    target = sources_dir / "geoboundaries"
    out, records = {}, []
    for cc in sorted({code.split("-")[0] for code in wanted}):
        if cc not in iso3:
            continue
        layers = {}
        for adm in LEVELS:
            features, licence, recs = _layer(iso3[cc], adm, target, force)
            layers[adm] = (features, licence)
            records += recs
        out.update(match_layers({k: v for k, v in wanted.items() if k.startswith(cc + "-")}, layers))
    return out, records
