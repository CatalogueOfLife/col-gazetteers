"""Which ids the iso gazetteer holds, their labels, and which ids are aliases.

The last committed `iso/labels.tsv` is the floor: every id in it stays, with its
label, because data in ChecklistBank may use it. New ids come only from
Wikidata's ISO 3166-2 codes (`P300`), current or withdrawn, and only when they
match the backend's pattern.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from common.download import SourceRecord
from common.wikidata import sparql_csv

ID_PATTERN = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,6})?$")
# ISO 3166-2 codes of a place that also has an ISO 3166-1 code, where Wikidata keeps the two on
# separate items or lacks P297. Includes the dual codes the old build curated (main:iso/sources.tsv).
DUAL_EXTRA = {
    "RS-KM": "XK",
    "CN-TW": "TW", "CN-HK": "HK", "CN-MO": "MO",
    "FR-GF": "GF", "FR-GP": "GP", "FR-MQ": "MQ", "FR-RE": "RE", "FR-YT": "YT",
    "FR-973": "GF", "FR-971": "GP", "FR-972": "MQ", "FR-974": "RE", "FR-976": "YT",
    "FR-BL": "BL", "FR-MF": "MF", "FR-NC": "NC", "FR-PF": "PF", "FR-PM": "PM", "FR-TF": "TF", "FR-WF": "WF",
    "NL-AW": "AW", "NL-CW": "CW", "NL-SX": "SX",
    "NO-21": "SJ", "NO-22": "SJ", "DK-GL": "GL", "FI-01": "AX", "GB-JSY": "JE",
    "US-AS": "AS", "US-GU": "GU", "US-MP": "MP", "US-PR": "PR", "US-UM": "UM", "US-VI": "VI",
}
# Codes kept for legacy data, as symlinks to the code that replaced them.
LEGACY_ALIASES = {"TP": "TL", "BQ-BO": "NL-BQ1", "BQ-SA": "NL-BQ2"}
# Deliberate fixes of labels carried over from the floor.
LABEL_OVERRIDES: dict[str, str] = {}

QUERY = """\
SELECT ?code ?item ?itemLabel ?alpha2 ?replacedByCode ?osm ?coord ?end WHERE {
  ?item wdt:P300 ?code .
  OPTIONAL { ?item wdt:P297 ?alpha2 }
  OPTIONAL { ?item wdt:P1366 ?rb . ?rb wdt:P300 ?replacedByCode }
  OPTIONAL { ?item wdt:P402 ?osm }
  OPTIONAL { ?item wdt:P625 ?coord }
  OPTIONAL { ?item wdt:P576|wdt:P582 ?end }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""

_POINT = re.compile(r"Point\(\s*([-0-9.eE]+)\s+([-0-9.eE]+)\s*\)")
_QID = re.compile(r"^Q[0-9]+$")


@dataclass(frozen=True)
class WdCode:
    code: str
    item: str
    label: str
    alpha2: str
    replaced_by: tuple[str, ...]
    osm_relation: int | None
    lon: float | None
    lat: float | None
    ended: bool


def read_floor(labels_tsv: Path, *, min_rows: int = 5000) -> dict[str, str]:
    """The committed id -> label map. Refuses a missing or suspiciously small file."""
    if not labels_tsv.is_file():
        raise RuntimeError(f"{labels_tsv} is missing: it is the floor of ids the rebuild must keep")
    floor = {}
    for line in labels_tsv.read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if cols[0]:
            floor[cols[0]] = cols[1] if len(cols) > 1 else ""
    if len(floor) < min_rows:
        raise RuntimeError(f"{labels_tsv} has only {len(floor)} ids, fewer than {min_rows}: refusing to use it as the floor")
    return floor


def parse_wikidata_csv(text: str) -> dict[str, WdCode]:
    """One WdCode per code from the query's CSV, which has a row per combination of values."""
    rows: dict[str, list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        rows.setdefault(row["code"], []).append(row)
    out = {}
    for code, group in rows.items():
        def first(key: str) -> str:
            return next((r[key] for r in group if r.get(key)), "")

        label = first("itemLabel")
        point = _POINT.search(first("coord"))
        osm = first("osm")
        out[code] = WdCode(
            code=code,
            item=first("item").rsplit("/", 1)[-1],
            label="" if _QID.match(label) else label,
            alpha2=first("alpha2"),
            replaced_by=tuple(dict.fromkeys(r["replacedByCode"] for r in group if r.get("replacedByCode"))),
            osm_relation=int(osm) if osm.isdigit() else None,
            lon=float(point.group(1)) if point else None,
            lat=float(point.group(2)) if point else None,
            ended=any(r.get("end") for r in group),
        )
    return out


def wikidata_codes(sources_dir: Path, *, force: bool) -> tuple[dict[str, WdCode], SourceRecord]:
    path, record = sparql_csv(
        QUERY, sources_dir / "wikidata_iso_3166_2.csv",
        role="iso-3166-2-wikidata", name="Wikidata SPARQL: every ISO 3166-2 code with its metadata",
        force=force,
    )
    return parse_wikidata_csv(path.read_text(encoding="utf-8")), record


def build_universe(floor: dict[str, str], wd: dict[str, WdCode]) -> dict[str, str]:
    """id -> label: every floor id with its label, plus new Wikidata codes that match the pattern."""
    labels = {**floor, **{k: v for k, v in LABEL_OVERRIDES.items() if k in floor}}
    for code, item in wd.items():
        if code not in labels and ID_PATTERN.match(code):
            labels[code] = item.label or code
    return labels


def dual_codes(wd: dict[str, WdCode], countries: set[str]) -> dict[str, str]:
    """Subdivision id -> the country code of the same place, for countries the gazetteer has.

    Exceptionally reserved codes such as AC (Ascension) or CP (Clipperton) are not
    countries here, so their subdivisions stay subdivisions.
    """
    dual = {code: item.alpha2 for code, item in wd.items() if "-" in code and item.alpha2}
    return {code: target for code, target in {**dual, **DUAL_EXTRA}.items() if target in countries}


def superseded(wd: dict[str, WdCode], ids: set[str]) -> dict[str, str]:
    """Withdrawn id -> its successor, where exactly one code replaced it."""
    return {
        code: item.replaced_by[0]
        for code, item in wd.items()
        if code in ids and len(item.replaced_by) == 1 and item.replaced_by[0] in ids and item.replaced_by[0] != code
    }
