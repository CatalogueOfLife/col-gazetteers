"""GBIF's country areas: the VLIZ EEZ-land union with GBIF's changes replayed.

`apply_gbif_changes` follows `import_political()` of gbif/geocode's
`database/import.sh` step by step and in the same order, as pinned in
`gbif_import_political.sh`. GBIF's geocoder then counts a row for every
country among its iso_ter1..3, after mapping alpha-3 to alpha-2 with GBIF's
country enumeration; `assign_codes` does the same.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import shapely
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry

from common.download import SourceRecord, download

from .geom import polys
from .model import Row

GBIF_OSM_RELATIONS = (3777250, 2088990, 52411, 913110, 1867188, 1650407)

ANTARCTIC_CAP = box(-180, -90, 180, -88)
SCHELDT_BOX = box(4.15, 51.008, 4.439, 51.408)
MIDWAY = shapely.from_wkt(
    "POLYGON ((-177.04996373497760942 31.60978833477749461, -177.83691262724664739 24.87185288052087273, "
    "-177.66332700965642744 24.37901220724413065, -177.36250316477767797 24.6621005824437951, "
    "-175.72778425823611315 31.30369873179363793, -176.27520866730711191 31.83088711396169757, "
    "-177.04996373497760942 31.60978833477749461))"
)
SOUTH_CHINA_SEA_CLAIM = 49003
DUPLICATE_JOINT_REGIMES = {48961, 21795, 48970, 48968, 50167, 48974, 48969, 48973, 48975, 48976,
                           48962, 48964, 48966, 48967}
UKRAINE_SECOND = ("territory2", "mrgid_ter2", "iso_ter2", "un_ter2", "sovereign2", "mrgid_sov2", "iso_sov2", "un_sov2")


def read_rows(union_zip: Path, work_dir: Path) -> list[Row]:
    """The union's rows, keys lower-cased, geometries repaired and not simplified."""
    unpacked = work_dir / "political"
    if unpacked.exists():
        shutil.rmtree(unpacked)
    unpacked.mkdir(parents=True)
    with zipfile.ZipFile(union_zip) as z:
        z.extractall(unpacked)
    shps = sorted(unpacked.rglob("*.shp"))
    if len(shps) != 1:
        raise ValueError(f"expected one shapefile in {union_zip}, found {[p.name for p in shps]}")
    out = work_dir / "political.geojson"
    out.unlink(missing_ok=True)
    subprocess.run(["ogr2ogr", "-f", "GeoJSON", "-makevalid", str(out), str(shps[0])], check=True)
    rows = []
    for feature in json.loads(out.read_text(encoding="utf-8"))["features"]:
        row = {k.lower(): v for k, v in (feature["properties"] or {}).items()}
        row["geom"] = polys(shape(feature["geometry"])) if feature["geometry"] else polys(None)
        rows.append(row)
    return rows


def fetch_osm_relations(ids: Iterable[int], sources_dir: Path, *, force: bool) -> tuple[dict[int, BaseGeometry], list[SourceRecord]]:
    """The polygons of OSM relations, from the OSM API as GBIF fetches them."""
    geoms, records = {}, []
    target = sources_dir / "osm-api"
    for rel in ids:
        path, record = download(
            f"https://api.openstreetmap.org/api/0.6/relation/{rel}/full", target,
            role=f"osm-relation-{rel}", name=f"OSM API: relation {rel} in full",
            filename=f"relation_{rel}.osm", force=force, upstream_version="", timeout=300,
        )
        records.append(record)
        out = target / f"relation_{rel}.geojson"
        out.unlink(missing_ok=True)
        subprocess.run(["ogr2ogr", "-f", "GeoJSON", str(out), str(path), "multipolygons"], check=True)
        found = [f for f in json.loads(out.read_text(encoding="utf-8"))["features"]
                 if f["properties"].get("osm_id") == str(rel) and f["geometry"]]
        if len(found) != 1:
            raise RuntimeError(f"OSM relation {rel} did not give exactly one polygon ({len(found)})")
        geoms[rel] = polys(shape(found[0]["geometry"]))
    return geoms, records


def _new_row(name: str, iso3: str, geom: BaseGeometry, mrgid=None) -> Row:
    row: Row = {"union": name, "territory1": name, "iso_ter1": iso3, "iso_sov1": iso3,
                "mrgid_eez": mrgid, "pol_type": "Added by GBIF", "geom": geom}
    for n in (2, 3):
        row[f"iso_ter{n}"] = row[f"iso_sov{n}"] = None
    return row


def _cut_out(rows: list[Row], osm: BaseGeometry, from_iso3: str, name: str, new_iso3: str, *, drop_empty: bool) -> list[Row]:
    sources = [r for r in rows if r.get("iso_ter1") == from_iso3]
    added = [_new_row(name, new_iso3, polys(r["geom"].intersection(osm))) for r in sources]
    if drop_empty:
        added = [r for r in added if not r["geom"].is_empty]
    for r in sources:
        r["geom"] = polys(r["geom"].difference(osm))
    return rows + added


def apply_gbif_changes(rows: list[Row], osm: Mapping[int, BaseGeometry]) -> list[Row]:
    """Replay GBIF's changes. A change whose OSM relation is not in `osm` is skipped."""
    rows = list(rows)

    def mrgid(m: int) -> list[Row]:
        return [r for r in rows if r.get("mrgid_eez") == m]

    # 1. Antarctica has no sovereign: give it ATA, and a cap over the pole.
    for r in mrgid(8489):
        r["iso_ter1"] = "ATA"
    rows.append(_new_row("Antarctica", "ATA", ANTARCTIC_CAP, 8489) | {"iso_sov1": None})

    # 2. Fujian Province (Kinmen, Matsu) from China to Taiwan.
    if 3777250 in osm:
        for r in mrgid(8486):
            r["geom"] = polys(r["geom"].difference(osm[3777250]))
        for r in mrgid(8321):
            r["geom"] = polys(r["geom"].union(osm[3777250]))

    # 3-5. Kosovo out of Serbia, Hong Kong and Macao out of China.
    if 2088990 in osm:
        rows = _cut_out(rows, osm[2088990], "SRB", "Kosovo", "XKX", drop_empty=False)
    if 913110 in osm:
        rows = _cut_out(rows, osm[913110], "CHN", "Hong Kong", "HKG", drop_empty=True)
    if 1867188 in osm:
        rows = _cut_out(rows, osm[1867188], "CHN", "Macao", "MAC", drop_empty=True)

    # 6. The Netherlands / Belgium water bit near Antwerp follows OSM's Belgium.
    if 52411 in osm:
        bit = polys(osm[52411].intersection(SCHELDT_BOX))
        for r in rows:
            if r.get("iso_ter1") == "BEL":
                r["geom"] = polys(r["geom"].union(bit))
            elif r.get("iso_ter1") == "NLD":
                r["geom"] = polys(r["geom"].difference(bit))

    # 7. Åland out of Finland.
    if 1650407 in osm:
        rows = _cut_out(rows, osm[1650407], "FIN", "Åland Islands", "ALA", drop_empty=False)

    # 8. Midway out of Hawaii; Navassa also counts for UM and JM.
    hawaii = mrgid(8453)
    rows += [_new_row("Midway Atoll", "UMI", polys(r["geom"].intersection(MIDWAY))) for r in hawaii]
    for r in hawaii:
        r["geom"] = polys(r["geom"].difference(MIDWAY))
    for r in mrgid(48951):
        r["iso_ter2"], r["iso_ter3"] = "UMI", "JAM"

    # 9. The South China Sea claim, and joint regimes that duplicate an EEZ.
    rows = [r for r in rows if r.get("mrgid_eez") != SOUTH_CHINA_SEA_CLAIM
            and r.get("mrgid_eez") not in DUPLICATE_JOINT_REGIMES]

    # 10. Missing territory codes take the sovereign's.
    for r in rows:
        for n in (1, 2, 3):
            if not r.get(f"iso_ter{n}"):
                r[f"iso_ter{n}"] = r.get(f"iso_sov{n}")

    # 11. Code corrections.
    for r in mrgid(62589):
        r["iso_ter1"], r["iso_ter2"] = "IOT", "MUS"
    for r in rows:
        if r.get("iso_ter1") in ("FLK", "SGS", "MYT", "ESH"):
            r["iso_ter2"] = r.get("iso_sov2")
    for r in mrgid(48946):
        r["iso_ter1"] = "ATF"
    for r in mrgid(48948):
        r["iso_ter1"] = "NCL"

    # 12. Ukraine is marked twice.
    for r in mrgid(5695):
        for key in UKRAINE_SECOND:
            r[key] = None

    return [r for r in rows if not r["geom"].is_empty]


def iso3_to_iso2(sources_dir: Path, *, force: bool) -> tuple[dict[str, str], SourceRecord]:
    """GBIF's own alpha-3 to alpha-2 table, plus Kosovo, as its geocoder uses."""
    path, record = download(
        "https://api.gbif.org/v1/enumeration/country", sources_dir,
        role="gbif-country-enumeration", name="GBIF API: country enumeration",
        filename="gbif_countries.json", force=force, upstream_version="", timeout=60,
    )
    table = {c["iso3"]: c["iso2"] for c in json.loads(path.read_text(encoding="utf-8"))}
    table["XKX"] = "XK"
    return table, record


def assign_codes(rows: list[Row], iso_map: Mapping[str, str]) -> set[str]:
    """Set each row's "codes" from iso_ter1..3, in order. Returns alpha-3 codes GBIF can't map."""
    unmapped = set()
    for r in rows:
        codes: list[str] = []
        for n in (1, 2, 3):
            iso3 = r.get(f"iso_ter{n}")
            if not iso3:
                continue
            if iso3 not in iso_map:
                unmapped.add(iso3)
            elif iso_map[iso3] not in codes:
                codes.append(iso_map[iso3])
        r["codes"] = codes
    return unmapped
