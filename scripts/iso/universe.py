"""Which ids the iso gazetteer holds, their labels, and which ids are aliases.

The last committed `iso/labels.tsv` is the floor: every id in it stays, with its
label, because data in ChecklistBank may use it. New ids come only from
Wikidata's ISO 3166-2 codes (`P300`), current or withdrawn, and only when they
match the backend's pattern.
"""

from __future__ import annotations

import csv
import difflib
import io
import re
import unicodedata
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
# Deliberate fixes of labels carried over from the floor. Each is checked against the outline.
LABEL_OVERRIDES: dict[str, str] = {
    # ISO 3166-2:MA (2019) gave MA-01..12 to the 12 regions of 2015. The floor still
    # names the 16 regions of 1997; MA-13..16, dropped from ISO, keep those.
    "MA-01": "Tanger-Tétouan-Al Hoceïma",
    "MA-02": "L'Oriental",
    "MA-03": "Fès-Meknès",
    "MA-04": "Rabat-Salé-Kénitra",
    "MA-05": "Béni Mellal-Khénifra",
    "MA-06": "Casablanca-Settat",
    "MA-07": "Marrakech-Safi",
    "MA-08": "Drâa-Tafilalet",
    "MA-09": "Souss-Massa",
    "MA-10": "Guelmim-Oued Noun",
    "MA-11": "Laâyoune-Sakia El Hamra",
    "MA-12": "Dakhla-Oued Ed-Dahab",
    # ISO 3166-2:IR (2020) renumbered the provinces; the floor kept the old numbering.
    "IR-00": "Markazi",
    "IR-01": "Gilan",
    "IR-02": "Mazandaran",
    "IR-03": "East Azerbaijan",
    "IR-04": "West Azerbaijan",
    "IR-05": "Kermanshah",
    "IR-06": "Khuzestan",
    "IR-07": "Fars",
    "IR-08": "Kerman",
    "IR-09": "Razavi Khorasan",
    "IR-10": "Isfahan",
    "IR-11": "Sistan and Baluchestan",
    "IR-12": "Kurdistan",
    "IR-13": "Hamadan",
    "IR-14": "Chaharmahal and Bakhtiari",
    "IR-15": "Lorestan",
    "IR-16": "Ilam",
    "IR-17": "Kohgiluyeh and Boyer-Ahmad",
    "IR-18": "Bushehr",
    "IR-19": "Zanjan",
    "IR-20": "Semnan",
    "IR-21": "Yazd",
    "IR-22": "Hormozgan",
    "IR-23": "Tehran",
    "IR-24": "Ardabil",
    "IR-25": "Qom",
    "IR-26": "Qazvin",
    "IR-28": "North Khorasan",
    "IR-30": "Alborz",
    # ISO 3166-2:UG numbers districts alphabetically within each batch; the floor was shifted.
    "UG-114": "Lyantonde",
    "UG-115": "Mityana",
    "UG-116": "Nakaseke",
    "UG-218": "Bududa",
    "UG-219": "Bukedea",
    "UG-220": "Bukwo",
    "UG-221": "Butaleja",
    "UG-222": "Kaliro",
    "UG-223": "Manafwa",
    "UG-224": "Namutumba",
    "UG-314": "Abim",
    "UG-315": "Amolatar",
    "UG-316": "Amuru",
    "UG-317": "Dokolo",
    "UG-318": "Kaabong",
    "UG-319": "Koboko",
    "UG-325": "Kole",
    "UG-326": "Lamwo",
    "UG-327": "Napak",
    "UG-328": "Nwoya",
    "UG-329": "Otuke",
    "UG-330": "Zombo",
    "UG-331": "Omoro",
    "UG-416": "Buliisa",
    "UG-417": "Ibanda",
    "UG-419": "Kiruhura",
    "UG-420": "Buhweju",
    "UG-421": "Kiryandongo",
    "UG-422": "Kyegegwa",
    "UG-423": "Mitooma",
    "UG-424": "Ntoroko",
    "UG-425": "Rubirizi",
    # Latvian municipalities of 2009-2021, which Natural Earth all names Valmiera.
    "LV-008": "Amata",
    "LV-009": "Ape",
    "LV-017": "Beverīna",
    "LV-019": "Burtnieki",
    "LV-021": "Cesvaine",
    "LV-030": "Ērgļi",
    "LV-039": "Jaunpiebalga",
    "LV-045": "Kocēni",
    "LV-055": "Līgatne",
    "LV-057": "Lubāna",
    "LV-060": "Mazsalaca",
    "LV-064": "Naukšēni",
    "LV-070": "Pārgauja",
    "LV-075": "Priekuļi",
    "LV-076": "Rauna",
    "LV-084": "Rūjiena",
    "LV-094": "Smiltene",
    "LV-096": "Strenči",
    "LV-102": "Varakļāni",
    "LV-104": "Vecpiebalga",
    # Madagascar's provinces until 2009, which Natural Earth names after one of their regions.
    "MG-D": "Antsiranana",
    "MG-F": "Fianarantsoa",
    "MG-M": "Mahajanga",
    "MG-U": "Toliara",
    # ISO's name, as for every other code. The floor's Armenian name reads as a position on a
    # disputed territory, which the gazetteer doesn't take (Azerbaijan has held the city since 2023).
    "AZ-XA": "Xankəndi",
    # Labels of another place, a capital, or the unit above or below.
    "BA-BIH": "Federation of Bosnia and Herzegovina",
    "BA-SRP": "Republika Srpska",
    "BT-31": "Sarpang",
    "CO-CUN": "Cundinamarca",
    "DO-07": "Elías Piña",
    "GB-CKT": "Cookstown",
    "GB-SHN": "St Helens",
    "GB-WRL": "Wirral",
    "GY-ES": "Essequibo Islands-West Demerara",
    "IT-GE": "Genoa",
    "MD-GA": "Gagauzia",
    "NP-KO": "Kosi",
    "PA-1": "Bocas del Toro",
    "PH-LAN": "Lanao del Norte",
    "PH-SCO": "South Cotabato",
    "SB-CH": "Choiseul",
    "TJ-SU": "Sughd",
    "VN-53": "Bắc Kạn",
    "VN-66": "Hưng Yên",
}
# Labels that differ from Wikidata's on purpose or only in their English form, so `label_drift` keeps quiet.
LABELS_REVIEWED = {"BY-MA", "FI-06", "FI-16", "FI-19", "KM-G", "MD-SN", "MT-45", "SA-02", "YE-SA"}

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
    """One WdCode per code from the query's CSV, which has a row per combination of values.

    A code can sit on several items, such as a withdrawn region and the current one that
    reused its code. The code is withdrawn only when every item it sits on has ended, only
    ended items name its successors, and a current item gives the label and coordinates.
    """
    rows: dict[str, list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        rows.setdefault(row["code"], []).append(row)
    out = {}
    for code, group in rows.items():
        def first(key: str) -> str:
            return next((r[key] for r in group if r.get(key)), "")

        items: dict[str, bool] = {}
        for r in group:
            items[r["item"]] = items.get(r["item"], False) or bool(r.get("end"))
        ended = all(items.values())
        group.sort(key=lambda r: items[r["item"]])  # a current item's label and coordinates first
        label = first("itemLabel")
        point = _POINT.search(first("coord"))
        osm = first("osm")
        out[code] = WdCode(
            code=code,
            item=first("item").rsplit("/", 1)[-1],
            label="" if _QID.match(label) else label,
            alpha2=first("alpha2"),
            replaced_by=tuple(dict.fromkeys(r["replacedByCode"] for r in group
                                            if r.get("replacedByCode") and items[r["item"]])),
            osm_relation=int(osm) if osm.isdigit() else None,
            lon=float(point.group(1)) if point else None,
            lat=float(point.group(2)) if point else None,
            ended=ended,
        )
    return out


def wikidata_codes(sources_dir: Path, *, force: bool) -> tuple[dict[str, WdCode], SourceRecord]:
    path, record = sparql_csv(
        QUERY, sources_dir / "wikidata_iso_3166_2.csv",
        role="iso-3166-2-wikidata", name="Wikidata SPARQL: every ISO 3166-2 code with its metadata",
        force=force,
    )
    wd = parse_wikidata_csv(path.read_text(encoding="utf-8"))
    check_wikidata(wd)
    return wd, record


# Wikidata had 5,352 codes in November 2026, 99% with coordinates and a label, 91% with an OSM
# relation. Far fewer means a truncated answer or a column the query no longer fills.
MIN_WIKIDATA_CODES = 5000
MIN_SHARE = {"coordinates": 0.9, "labels": 0.9, "OSM relations": 0.8}


def check_wikidata(wd: dict[str, WdCode]) -> None:
    if len(wd) < MIN_WIKIDATA_CODES:
        raise RuntimeError(f"Wikidata gave {len(wd)} codes, fewer than {MIN_WIKIDATA_CODES}")
    have = {
        "coordinates": sum(1 for w in wd.values() if w.lon is not None and w.lat is not None),
        "labels": sum(1 for w in wd.values() if w.label),
        "OSM relations": sum(1 for w in wd.values() if w.osm_relation),
    }
    for what, count in have.items():
        if count < MIN_SHARE[what] * len(wd):
            raise RuntimeError(f"only {count} of {len(wd)} Wikidata codes have {what}, "
                               f"fewer than {MIN_SHARE[what]:.0%}: has the query changed?")


def build_universe(floor: dict[str, str], wd: dict[str, WdCode]) -> dict[str, str]:
    """id -> label: every floor id with its label, plus new Wikidata codes that match the pattern."""
    labels = {**floor, **{k: v for k, v in LABEL_OVERRIDES.items() if k in floor}}
    for code, item in wd.items():
        if code not in labels and ID_PATTERN.match(code):
            labels[code] = item.label or code
    return labels


# Words that only say what kind of unit it is, left out when comparing names.
_UNIT_WORDS = frozenset(
    "autonomous canton capital city county department departement district governorate island islands kraj "
    "municipality oblast okres parish prefecture province provincia rayon region regione republic state "
    "territory voivodeship and of the de la le du des di".split())


def _name_words(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(w for w in re.findall(r"[a-z0-9]+", ascii_name) if w not in _UNIT_WORDS)


def label_drift(labels: dict[str, str], wd: dict[str, WdCode], *,
                reviewed: set[str] | frozenset[str] = frozenset()) -> list[tuple[str, str, str]]:
    """(id, label, Wikidata's label) for subdivisions whose label names another place than Wikidata's.

    Differences in accents, unit words ("Province") or wording that keeps most letters don't count,
    so what remains is mostly codes ISO gave to another place, or a label that was wrong from the start.
    """
    drift = []
    for code, label in sorted(labels.items()):
        item = wd.get(code)
        if "-" not in code or code in reviewed or not item or not item.label:
            continue
        ours, theirs = _name_words(label), _name_words(item.label)
        if ours in theirs or theirs in ours or difflib.SequenceMatcher(None, ours, theirs).ratio() >= 0.5:
            continue
        drift.append((code, label, item.label))
    return drift


def dual_codes(wd: dict[str, WdCode], countries: set[str]) -> dict[str, str]:
    """Subdivision id -> the country code of the same place, for countries the gazetteer has.

    Exceptionally reserved codes such as AC (Ascension) or CP (Clipperton) are not
    countries here, so their subdivisions stay subdivisions.
    """
    dual = {code: item.alpha2 for code, item in wd.items() if "-" in code and item.alpha2}
    return {code: target for code, target in {**dual, **DUAL_EXTRA}.items() if target in countries}


def aliases_for(labels: dict[str, str], wd: dict[str, WdCode]) -> dict[str, str]:
    """Ids that are always links: dual codes, and legacy ones, which win.

    Superseded codes are left out: whether one becomes a link depends on whether any
    source still has its own shape, which only the build knows (see `superseded`).
    """
    countries = {code for code in labels if len(code) == 2}
    merged = {**dual_codes(wd, countries), **LEGACY_ALIASES}
    return {code: target for code, target in merged.items() if code in labels and target in labels}


def superseded(wd: dict[str, WdCode], ids: set[str], shaped: set[str] | frozenset[str] = frozenset()) -> dict[str, str]:
    """Withdrawn id -> the one code that replaced it, for ids no source has a shape of.

    As in the earlier build, a withdrawn code with a shape of its own keeps it, and a
    current code is never a link, whatever Wikidata's "replaced by" says.
    """
    return {
        code: item.replaced_by[0]
        for code, item in wd.items()
        if code in ids and code not in shaped and item.ended and len(item.replaced_by) == 1
        and item.replaced_by[0] in ids and item.replaced_by[0] != code
    }
