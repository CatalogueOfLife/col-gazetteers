"""Tests for the iso build on small fixtures, no downloads or data needed.

Run with `python scripts/test_iso.py` (no test framework required, like
test_wikidata.py). Exit code 0 on success, an assertion error otherwise.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shapely import STRtree  # noqa: E402
from shapely.geometry import box, mapping  # noqa: E402

from common.config import Config  # noqa: E402
from iso import (  # noqa: E402
    checks, countries, derived, fit, gbif_drift, gbif_sources, geoboundaries, natural_earth, osm, placeholder,
    political, universe, write,
)
from iso.model import Candidate, Countries, FitResult  # noqa: E402


SCRIPT = """#!/bin/bash
function import_political() {
    curl -LSs --fail https://download.gbif.org/MapDataMirror/2026/10/EEZ_land_union_v4_202410.zip
    echo "UPDATE political SET iso_ter1 = 'ATA' WHERE mrgid_eez = 8489;" | exec_psql
}

function import_mr_eez() {
    curl -LSs --fail https://download.gbif.org/MapDataMirror/2026/10/eez_v12.gpkg
}

function import_gadm() {
    curl -LSs --fail https://download.gbif.org/MapDataMirror/2022/08/gadm_410-gpkg.zip
}
"""


def _without_download(replacement):
    """Swap gbif_sources.download for `replacement`; returns a restore callable."""
    saved = gbif_sources.download
    gbif_sources.download = replacement
    return lambda: setattr(gbif_sources, "download", saved)


# --- gbif_drift -------------------------------------------------------------

def test_extract_function() -> None:
    body = gbif_drift.extract_function(SCRIPT, "import_mr_eez")
    assert body.startswith("function import_mr_eez() {"), body
    assert body.rstrip().endswith("}"), body
    assert "eez_v12.gpkg" in body
    assert "EEZ_land_union" not in body and "gadm" not in body, body


def test_drift_warns_on_changed_function() -> None:
    upstream = SCRIPT.replace("mrgid_eez = 8489", "mrgid_eez = 8490")
    warnings = gbif_drift.drift_warnings(SCRIPT, upstream)
    assert len(warnings) == 1, warnings
    assert "import_political" in warnings[0], warnings


def test_drift_warns_on_changed_mirror_file() -> None:
    upstream = SCRIPT.replace("EEZ_land_union_v4_202410.zip", "EEZ_land_union_v5_202610.zip")
    warnings = gbif_drift.drift_warnings(SCRIPT, upstream)
    assert any("EEZ_land_union_v5_202610.zip" in w for w in warnings), warnings


def test_drift_ignores_other_functions() -> None:
    upstream = SCRIPT.replace("2022/08/gadm_410-gpkg.zip", "2026/11/gadm_411-gpkg.zip")
    assert gbif_drift.drift_warnings(SCRIPT, upstream) == []


# --- gbif_sources -----------------------------------------------------------

def test_locate_prefers_local_files() -> None:
    def no_network(*args, **kwargs):
        raise AssertionError("downloaded although both files are local")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / gbif_sources.UNION_ZIP).write_bytes(b"zip")
        (d / gbif_sources.EEZ_GPKG).write_bytes(b"gpkg")
        restore = _without_download(no_network)
        try:
            union, eez, records = gbif_sources.locate_gbif_files(d)
        finally:
            restore()
    assert union == d / gbif_sources.UNION_ZIP and eez == d / gbif_sources.EEZ_GPKG
    assert [r.size_bytes for r in records] == [3, 4], records
    assert records[0].md5 == hashlib.md5(b"zip").hexdigest()


def test_locate_explains_how_to_get_files() -> None:
    def forbidden(*args, **kwargs):
        raise RuntimeError("403 Forbidden")

    with tempfile.TemporaryDirectory() as tmp:
        restore = _without_download(forbidden)
        try:
            gbif_sources.locate_gbif_files(Path(tmp))
        except RuntimeError as e:
            want = "gh release download gbif-mapdatamirror-2026-10 -R CatalogueOfLife/gazetteer-sources"
            assert want in str(e), e
        else:
            raise AssertionError("no error although the files are nowhere")
        finally:
            restore()


# --- political ----------------------------------------------------------------

def _row(mrgid, iso_ter1, geom, **fields) -> dict:
    row = {"union": f"row {mrgid}", "mrgid_eez": mrgid, "pol_type": "Union EEZ and country", "geom": geom}
    for n in (1, 2, 3):
        row[f"iso_ter{n}"] = None
        row[f"iso_sov{n}"] = None
    row["iso_ter1"] = iso_ter1
    row.update(fields)
    return row


def _by(rows, **match) -> list[dict]:
    return [r for r in rows if all(r.get(k) == v for k, v in match.items())]


def test_read_rows_lowercases_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"ISO_TER1": "DEU", "x_1": 10.5, "MRGID_EEZ": 5669},
             "geometry": mapping(box(5, 47, 15, 55))},
            {"type": "Feature", "properties": {"ISO_TER1": "FRA", "x_1": 2.0, "MRGID_EEZ": 5677},
             "geometry": mapping(box(-5, 42, 8, 51))},
        ]}
        (d / "in.geojson").write_text(json.dumps(fc))
        subprocess.run(["ogr2ogr", "-f", "ESRI Shapefile", str(d / "union.shp"), str(d / "in.geojson")], check=True)
        with zipfile.ZipFile(d / "union.zip", "w") as z:
            for part in d.glob("union.*"):
                if part.suffix != ".zip":
                    z.write(part, f"union/{part.name}")
        rows = political.read_rows(d / "union.zip", d / "work")
    assert len(rows) == 2, rows
    assert {"iso_ter1", "x_1", "mrgid_eez", "geom"} <= set(rows[0]), rows[0].keys()
    assert {r["iso_ter1"] for r in rows} == {"DEU", "FRA"}
    assert abs(rows[0]["geom"].area - 80) < 1e-9 or abs(rows[0]["geom"].area - 117) < 1e-9


def test_kosovo_cut_out_of_serbia() -> None:
    rows = political.apply_gbif_changes([_row(5685, "SRB", box(0, 0, 10, 10))], {2088990: box(5, 5, 7, 7)})
    xk = _by(rows, iso_ter1="XKX")
    assert len(xk) == 1 and abs(xk[0]["geom"].area - 4) < 1e-9, xk
    assert abs(_by(rows, iso_ter1="SRB")[0]["geom"].area - 96) < 1e-9


def test_fujian_moves_to_taiwan() -> None:
    rows = [_row(8486, "CHN", box(0, 0, 10, 10)), _row(8321, "TWN", box(20, 0, 30, 10))]
    rows = political.apply_gbif_changes(rows, {3777250: box(9, 0, 10, 1)})
    assert abs(_by(rows, mrgid_eez=8486)[0]["geom"].area - 99) < 1e-9
    assert abs(_by(rows, mrgid_eez=8321)[0]["geom"].area - 101) < 1e-9


def test_empty_hong_kong_rows_dropped() -> None:
    rows = [_row(8486, "CHN", box(0, 0, 10, 10)), _row(49010, "CHN", box(50, 50, 51, 51))]
    rows = political.apply_gbif_changes(rows, {913110: box(1, 1, 2, 2)})
    assert len(_by(rows, iso_ter1="HKG")) == 1, _by(rows, iso_ter1="HKG")


def test_deleted_rows() -> None:
    rows = [_row(m, "XXX", box(0, 0, 1, 1)) for m in (49003, 48961, 100)]
    kept = {r["mrgid_eez"] for r in political.apply_gbif_changes(rows, {})}
    assert 49003 not in kept and 48961 not in kept and 100 in kept, kept


def test_fill_happens_before_corrections() -> None:
    rows = political.apply_gbif_changes([_row(62589, None, box(0, 0, 1, 1), iso_sov1="GBR")], {})
    chagos = _by(rows, mrgid_eez=62589)[0]
    assert (chagos["iso_ter1"], chagos["iso_ter2"]) == ("IOT", "MUS"), chagos


def test_codes_count_every_claimant() -> None:
    rows = [_row(50000, "ESH", box(0, 0, 1, 1), iso_sov1="ESH", iso_sov2="MAR")]
    rows = political.apply_gbif_changes(rows, {})
    unmapped = political.assign_codes(rows, {"ESH": "EH", "MAR": "MA", "ATA": "AQ"})
    assert _by(rows, mrgid_eez=50000)[0]["codes"] == ["EH", "MA"], rows
    assert unmapped == set(), unmapped


def test_antarctica_cap() -> None:
    rows = political.apply_gbif_changes([_row(8489, None, box(-180, -90, 180, -60))], {})
    ata = _by(rows, iso_ter1="ATA")
    assert len(ata) == 2, ata
    assert any(r["geom"].equals(box(-180, -90, 180, -88)) for r in ata), ata


# --- countries ----------------------------------------------------------------

def _coded(codes, geom, pol_type="Union EEZ and country", union="row") -> dict:
    return {"union": union, "pol_type": pol_type, "codes": list(codes), "geom": geom}


def test_land_of_subtracts_water() -> None:
    water = [box(5, 0, 10, 10)]
    land = countries.land_of(box(0, 0, 10, 10), water, STRtree(water))
    assert abs(land.area - 50) < 1e-9, land.area


def test_overlapping_claim_counts_for_both() -> None:
    rows = [_coded(["EH", "MA"], box(0, 0, 2, 2), "Overlapping claim", "Western Sahara"),
            _coded(["MA"], box(2, 0, 4, 2), union="Morocco")]
    c = countries.build_countries(rows, [], workers=1)
    assert set(c.land) == set(c.eez) == {"EH", "MA"}, (c.land, c.eez)
    assert abs(c.eez["MA"].area - 8) < 1e-9 and abs(c.land["EH"].area - 4) < 1e-9
    assert c.rows["EH"] == ["Western Sahara (Overlapping claim)"], c.rows


def test_land_within_eez() -> None:
    water = [box(1, 0, 2, 2)]
    rows = [_coded(["AA"], box(0, 0, 2, 2)), _coded(["BB"], box(2, 0, 3, 1), "Landlocked country"),
            _coded(["AQ"], political.ANTARCTIC_CAP, "Added by GBIF", "Antarctica")]
    c = countries.build_countries(rows, water + [box(-180, -90, 180, -89)], workers=2)
    assert abs(c.land["AA"].area - 2) < 1e-9, c.land["AA"].area
    assert c.land["AQ"].equals(political.ANTARCTIC_CAP), "the polar cap is land, whatever the water says"
    for code in c.land:
        assert c.land[code].difference(c.eez[code]).area < 1e-9, code


def test_historic_is_union_of_successors() -> None:
    succ = ("SI", "HR", "BA", "RS", "ME", "MK", "XK", "CW", "SX", "BQ")
    shapes = {code: box(i, 0, i + 1, 1) for i, code in enumerate(succ)}
    c = Countries(land=dict(shapes), eez=dict(shapes), rows={})
    countries.add_historic(c)
    assert abs(c.land["YU"].area - 7) < 1e-9 and abs(c.eez["YU"].area - 7) < 1e-9
    assert abs(c.land["AN"].area - 3) < 1e-9


# --- universe -----------------------------------------------------------------

def _wd(code, label="", alpha2="", replaced_by=(), osm=None, ended=False) -> "universe.WdCode":
    return universe.WdCode(code=code, item="Q1", label=label, alpha2=alpha2, replaced_by=tuple(replaced_by),
                           osm_relation=osm, lon=None, lat=None, ended=ended)


def test_floor_refuses_missing_or_small_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "labels.tsv"
        small = Path(tmp) / "small.tsv"
        small.write_text("DE\tGermany\nFR\tFrance\nIT\tItaly\n", encoding="utf-8")
        for path in (missing, small):
            try:
                universe.read_floor(path)
            except RuntimeError:
                continue
            raise AssertionError(f"{path.name} was accepted as the floor")
        assert universe.read_floor(small, min_rows=3) == {"DE": "Germany", "FR": "France", "IT": "Italy"}


def test_parse_wikidata_csv() -> None:
    text = (
        "code,item,itemLabel,alpha2,replacedByCode,osm,coord,end\n"
        "IN-DD,http://www.wikidata.org/entity/Q66743,Daman and Diu,,IN-DH,1950000,Point(72.8 20.4),2020-01-26T00:00:00Z\n"
        "IN-DD,http://www.wikidata.org/entity/Q66743,Daman and Diu,,IN-DH,1950000,Point(72.8 20.4),\n"
        "FR-GF,http://www.wikidata.org/entity/Q3769,French Guiana,GF,,1260551,,\n"
        "XX-1,http://www.wikidata.org/entity/Q999,Q999,,,,,\n"
    )
    wd = universe.parse_wikidata_csv(text)
    assert wd["IN-DD"] == universe.WdCode("IN-DD", "Q66743", "Daman and Diu", "", ("IN-DH",), 1950000, 72.8, 20.4, True)
    assert wd["FR-GF"].alpha2 == "GF" and wd["FR-GF"].osm_relation == 1260551 and not wd["FR-GF"].ended
    assert wd["XX-1"].label == "", "a Q-id stands in for a missing English label"


def test_universe_keeps_every_floor_id() -> None:
    labels = universe.build_universe({"XZ": "International waters", "DE-BE": "Berlin"}, {"DE-BE": _wd("DE-BE", "Berlin")})
    assert labels["XZ"] == "International waters" and labels["DE-BE"] == "Berlin", labels


def test_floor_label_wins() -> None:
    labels = universe.build_universe({"US-DC": "District of Columbia"}, {"US-DC": _wd("US-DC", "Washington, D.C.")})
    assert labels["US-DC"] == "District of Columbia", labels


def test_new_ids_filtered_by_pattern() -> None:
    wd = {"BY-BR-IN": _wd("BY-BR-IN", "Ivanava"), "DZ-59": _wd("DZ-59", "Aflou"), "DZ-60": _wd("DZ-60")}
    labels = universe.build_universe({"DZ": "Algeria"}, wd)
    assert "BY-BR-IN" not in labels, labels
    assert labels["DZ-59"] == "Aflou" and labels["DZ-60"] == "DZ-60", labels


def test_dual_codes() -> None:
    wd = {"FR-GF": _wd("FR-GF", "French Guiana", alpha2="GF"), "FR-75": _wd("FR-75", "Paris")}
    dual = universe.dual_codes(wd, {"GF", "XK"})
    assert dual["FR-GF"] == "GF" and "FR-75" not in dual, dual
    assert dual["RS-KM"] == "XK", "DUAL_EXTRA is merged in"


def test_dual_codes_only_point_to_countries() -> None:
    wd = {"SH-AC": _wd("SH-AC", "Ascension", alpha2="AC"), "FR-GF": _wd("FR-GF", alpha2="GF")}
    dual = universe.dual_codes(wd, {"GF"})
    assert "SH-AC" not in dual, "AC is only reserved, not a country of the gazetteer"
    assert dual["FR-GF"] == "GF" and "RS-KM" not in dual, dual


def test_aliases_for() -> None:
    labels = {"FR": "France", "GF": "French Guiana", "FR-GF": "Guyane", "IN-DD": "Daman", "IN-DH": "Dadra",
              "TP": "East Timor", "TL": "Timor-Leste", "BQ-BO": "Bonaire"}
    wd = {"FR-GF": _wd("FR-GF", alpha2="GF", replaced_by=["FR-973"]), "IN-DD": _wd("IN-DD", replaced_by=["IN-DH"])}
    aliases = universe.aliases_for(labels, wd)
    assert aliases == {"FR-GF": "GF", "IN-DD": "IN-DH", "TP": "TL"}, aliases


def test_superseded_only_one_to_one() -> None:
    wd = {"IN-DD": _wd("IN-DD", replaced_by=["IN-DH"]), "CU-02": _wd("CU-02", replaced_by=["CU-15", "CU-16"])}
    aliases = universe.superseded(wd, {"IN-DD", "IN-DH", "CU-02", "CU-15", "CU-16"})
    assert aliases == {"IN-DD": "IN-DH"}, aliases


# --- osm ----------------------------------------------------------------------

def _relation(rel_id, code, ring, **tags) -> dict:
    tags = {"boundary": "administrative", "admin_level": "4", "ISO3166-2": code, "name": f"r{rel_id}", **tags}
    return {"type": "relation", "id": rel_id, "tags": tags, "members": [
        {"type": "way", "ref": rel_id * 10, "role": "outer", "geometry": [{"lon": x, "lat": y} for x, y in ring]}]}


def _square(x0, y0, size=1.0):
    return [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size), (x0, y0)]


def test_administrative_wins_over_historic() -> None:
    raw = {"elements": [_relation(1, "XX-A", _square(0, 0, 3), boundary="historic"), _relation(2, "XX-A", _square(5, 5))]}
    assert osm.to_candidates(raw)["XX-A"].source == "osm-relation-2"


def test_lower_admin_level_wins() -> None:
    raw = {"elements": [_relation(1, "XX-A", _square(0, 0, 3), admin_level="6"), _relation(2, "XX-A", _square(5, 5))]}
    cand = osm.to_candidates(raw)["XX-A"]
    assert cand.source == "osm-relation-2" and cand.resolution == "osm" and cand.label == "r2", cand


def test_multi_code_tag() -> None:
    raw = {"elements": [_relation(3, "XX-A;XX-B", _square(0, 0), **{"name:en": "Both"})]}
    cands = osm.to_candidates(raw)
    assert set(cands) == {"XX-A", "XX-B"} and cands["XX-B"].label == "Both", cands


def test_invalid_relation_is_repaired_or_skipped() -> None:
    bow_tie = [(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]
    cands = osm.to_candidates({"elements": [_relation(4, "XX-C", bow_tie), _relation(5, "XX-D", _square(2, 2))]})
    assert "XX-D" in cands
    if "XX-C" in cands:
        assert cands["XX-C"].geom.is_valid and not cands["XX-C"].geom.is_empty


def test_unwrap_antimeridian() -> None:
    from shapely.geometry import Polygon
    wrapped = Polygon([(179, 0), (-179, 0), (-179, 1), (179, 1)])  # 2° wide, written across ±180
    fixed = osm.unwrap_antimeridian(wrapped)
    xs = [x for part in fixed.geoms for x, _ in part.exterior.coords]
    assert fixed.geom_type == "MultiPolygon" and min(xs) >= -180 and max(xs) <= 180, fixed
    assert abs(fixed.area - 2) < 1e-9, fixed.area
    assert osm.unwrap_antimeridian(box(10, 0, 20, 5)).equals(box(10, 0, 20, 5)), "ordinary shapes stay"


def test_osm_fetch_failure_fails_the_build() -> None:
    def fake_fetch(query, cache_path, **kwargs):
        if '"^(XX)-"' in query:
            raise RuntimeError("504 Gateway Timeout")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text('{"elements": []}', encoding="utf-8")
        return cache_path, None

    saved = osm.overpass_fetch
    osm.overpass_fetch = fake_fetch
    try:
        with tempfile.TemporaryDirectory() as tmp:
            osm.fetch_osm(["AA", "XX", "BB"], [], Path(tmp), force=False, batch=1)
    except RuntimeError as e:
        assert "XX" in str(e) and "AA" not in str(e), e
    else:
        raise AssertionError("a failed country did not fail the fetch")
    finally:
        osm.overpass_fetch = saved


def test_failed_batch_is_split_until_single_countries() -> None:
    def fake_fetch(query, cache_path, **kwargs):
        countries = query.split('"^(')[1].split(')-"')[0].split("|")
        if "XX" in countries or ("BB" in countries and len(countries) > 1):
            raise RuntimeError("HTTP 502")  # XX always fails; BB only in company, as if too big
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text('{"elements": []}', encoding="utf-8")
        return cache_path, None

    saved = osm.overpass_fetch
    osm.overpass_fetch = fake_fetch
    try:
        with tempfile.TemporaryDirectory() as tmp:
            paths, _ = osm.fetch_osm(["AA", "BB", "CC"], [], Path(tmp), force=False, batch=4)
            assert len(paths) == 3 and all(p.is_file() for p in paths), paths
            try:
                osm.fetch_osm(["AA", "BB", "CC", "XX"], [], Path(tmp), force=False, batch=4)
            except RuntimeError as e:
                assert "XX" in str(e) and not any(cc in str(e) for cc in ("AA", "BB", "CC")), e
            else:
                raise AssertionError("XX failed on its own but the fetch succeeded")
    finally:
        osm.overpass_fetch = saved


def test_cached_pieces_are_reused_without_asking_again() -> None:
    def no_network(query, cache_path, **kwargs):
        raise AssertionError(f"queried Overpass for {query}")

    saved = osm.overpass_fetch
    osm.overpass_fetch = no_network
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "osm"
            target.mkdir()
            for name in ("countries_AA.json", "countries_BB-CC.json"):
                (target / name).write_text('{"elements": []}', encoding="utf-8")
            paths, _ = osm.fetch_osm(["AA", "BB", "CC"], [], Path(tmp), force=False, batch=3)
            assert sorted(p.name for p in paths) == ["countries_AA.json", "countries_BB-CC.json"], paths
            assert sorted(p.name for p in osm.cached_answers(target)) == ["countries_AA.json", "countries_BB-CC.json"]
    finally:
        osm.overpass_fetch = saved


def test_snapshot_round_trip() -> None:
    doc = {"elements": [_relation(1, "XX-A", _square(0, 0), **{"name:en": "Alpha"}),
                        _relation(2, "XX-B;XX-C", _square(2, 2), admin_level="6")]}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / osm.SNAPSHOT
        items = [*osm.relation_geoms(doc), *osm.relation_geoms(doc)]
        assert osm.write_snapshot(items, path) == 2, "a relation in two documents is written once"
        assert path.read_bytes()[:2] == b"\x1f\x8b", "gzip"
        items = osm.read_snapshot(path)
    tags = {rel_id: t for rel_id, t, _ in items}
    assert sorted(tags) == [1, 2] and tags[1]["name:en"] == "Alpha" and tags[2]["ISO3166-2"] == "XX-B;XX-C", tags
    from_snapshot, from_doc = osm.candidates_from(items), osm.to_candidates(doc)
    assert {k: v.source for k, v in from_snapshot.items()} == {k: v.source for k, v in from_doc.items()}
    assert set(from_snapshot) == {"XX-A", "XX-B", "XX-C"} and from_snapshot["XX-A"].label == "Alpha"


def test_snapshot_simplifies() -> None:
    import math
    ring = [(math.cos(2 * math.pi * i / 1000), math.sin(2 * math.pi * i / 1000)) for i in range(1000)]
    doc = {"elements": [_relation(3, "XX-D", ring + [ring[0]])]}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / osm.SNAPSHOT
        osm.write_snapshot(osm.relation_geoms(doc), path)
        [(_, _, geom)] = osm.read_snapshot(path)
    import shapely as _shapely
    assert _shapely.get_num_coordinates(geom) < 1000, _shapely.get_num_coordinates(geom)
    assert abs(geom.area - math.pi) < 0.01, geom.area


def test_countries_are_batched() -> None:
    queries = []

    def fake_fetch(query, cache_path, **kwargs):
        queries.append(query)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text('{"elements": []}', encoding="utf-8")
        return cache_path, None

    saved = osm.overpass_fetch
    osm.overpass_fetch = fake_fetch
    try:
        with tempfile.TemporaryDirectory() as tmp:
            paths, _ = osm.fetch_osm(["AA", "BB", "CC"], [], Path(tmp), force=False, batch=2)
            assert all(isinstance(p, Path) and p.is_file() for p in paths), paths
    finally:
        osm.overpass_fetch = saved
    assert len(queries) == 2 and len(paths) == 2, queries
    assert '"^(AA|BB)-"' in queries[0] and '"^(CC)-"' in queries[1], queries
    assert "[maxsize:" in queries[0], "big batches need more than Overpass's default 512 MB"


# --- fallbacks ----------------------------------------------------------------

def test_ne_keyed_by_iso_3166_2() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"iso_3166_2": "XX-A", "name": "Alpha", "name_en": "Alpha Province"},
             "geometry": mapping(box(0, 0, 1, 1))},
            {"type": "Feature", "properties": {"iso_3166_2": "-99", "name": "Nowhere", "name_en": None},
             "geometry": mapping(box(2, 0, 3, 1))},
        ]}
        (d / "ne.geojson").write_text(json.dumps(fc))
        subprocess.run(["ogr2ogr", "-f", "ESRI Shapefile", str(d / "ne.shp"), str(d / "ne.geojson")], check=True)
        cands = natural_earth.read_natural_earth(d / "ne.shp", d / "work")
    assert set(cands) == {"XX-A"}, cands
    assert cands["XX-A"].resolution == "natural-earth" and cands["XX-A"].label == "Alpha Province"
    assert abs(cands["XX-A"].geom.area - 1) < 1e-9


def _gb_feature(name, geom, iso="") -> dict:
    return {"type": "Feature", "properties": {"shapeName": name, "shapeISO": iso}, "geometry": mapping(geom)}


def test_gb_name_match_prefers_adm2() -> None:
    layers = {
        "ADM1": ([_gb_feature("Berat", box(0, 0, 10, 10), "AL-01")], "CC BY 4.0"),
        "ADM2": ([_gb_feature("Berat", box(0, 0, 1, 1))], "Creative Commons Attribution 2.5 Generic"),
    }
    cands = geoboundaries.match_layers({"AL-BR": "Berat District", "AL-01": "Berat County"}, layers)
    assert abs(cands["AL-BR"].geom.area - 1) < 1e-9, "the district, not the county of the same name"
    assert "Attribution 2.5" in cands["AL-BR"].note and cands["AL-BR"].resolution == "geoboundaries"
    assert abs(cands["AL-01"].geom.area - 100) < 1e-9, "shapeISO matches first"


def test_gb_never_matches_adm3() -> None:
    layers = {"ADM1": ([], "x"), "ADM2": ([], "x"), "ADM3": ([_gb_feature("Arta", box(0, 0, 1, 1))], "x")}
    assert geoboundaries.match_layers({"GR-31": "Arta Prefecture"}, layers) == {}


def test_derive_needs_all_parts() -> None:
    cands = derived.derive({"GB-ENG": box(0, 0, 2, 2), "GB-WLS": box(-1, 0, 0, 1)})
    assert "GB-EAW" in cands and "GB-GBN" not in cands, cands
    assert abs(cands["GB-EAW"].geom.area - 5) < 1e-9 and cands["GB-EAW"].resolution == "derived"
    assert "FR-COR" in derived.DERIVED and derived.DERIVED["FR-H"] == ("FR-2A", "FR-2B")


def test_placeholder_falls_back_to_centroid() -> None:
    land = box(10, 10, 20, 20)
    centroid = placeholder.placeholder("XX-1", None, land)
    assert centroid.resolution == "placeholder-country-centroid"
    assert centroid.geom.contains(land.representative_point())
    wd = universe.WdCode("XX-2", "Q2", "Two", "", (), None, 15.0, 12.0, False)
    circle = placeholder.placeholder("XX-2", wd, land)
    assert circle.resolution == "placeholder-circle" and abs(circle.geom.centroid.x - 15) < 1e-6
    assert abs(circle.geom.area - 3.14159 * 0.25) < 0.01, circle.geom.area


# --- fit, write, checks -------------------------------------------------------

def _cand(geom, resolution="osm", label="") -> Candidate:
    return Candidate(geom, f"src-{resolution}", resolution, label=label)


def test_fit_inside_land_is_kept_whole() -> None:
    r = fit.fit([_cand(box(1, 1, 2, 2))], box(0, 0, 10, 10), box(-5, -5, 15, 15), {})
    assert r.mode == "land" and r.geom.equals(box(1, 1, 2, 2)), r


def test_fit_island_falls_back_to_eez_and_is_absorbed() -> None:
    land, eez = box(0, 0, 10, 10), box(-5, -5, 20, 20)
    island = _cand(box(12, 12, 13, 13))
    circle = _cand(placeholder.circle(5, 5), "placeholder-circle")
    r = fit.fit([island, circle], land, eez, {})
    assert r.mode == "eez" and r.candidate is island, "a real outline beats a placeholder that touches land"
    c = Countries(land={"AA": land}, eez={"AA": eez})
    fit.absorb(c, ["AA"], r.geom)
    assert abs(c.land["AA"].area - 101) < 1e-9, c.land["AA"].area


def test_fit_tries_next_source() -> None:
    mostly_sea = _cand(box(9.5, 0, 19.5, 10))  # 5% on land
    ne = _cand(box(1, 0, 11, 10), "natural-earth")  # 90% on land
    r = fit.fit([mostly_sea, ne], box(0, 0, 10, 10), box(-50, -50, 50, 50), {})
    assert r.mode == "land" and r.candidate is ne, r


def test_fit_conflict_keeps_shape_and_names_holder() -> None:
    likoma = _cand(box(20, 20, 21, 21))
    circle = _cand(placeholder.circle(20.5, 20.5), "placeholder-circle")
    holders = {"MW": box(0, 0, 10, 10), "MZ": box(15, 15, 30, 30)}
    r = fit.fit([likoma, circle], box(0, 0, 10, 10), box(0, 0, 10, 10), holders)
    assert r.mode == "conflict" and r.candidate is likoma and r.geom.equals(likoma.geom), r
    assert r.holder == "MZ", r.holder


def test_placeholder_kept_however_small() -> None:
    tiny = box(0, 0, 0.01, 0.01)
    r = fit.fit([_cand(placeholder.circle(0, 0), "placeholder-circle")], tiny, tiny, {})
    assert r.mode == "land" and abs(r.geom.area - 0.0001) < 1e-9, r


def test_territories() -> None:
    assert fit.territories("FR", {"FR-GF": "GF", "FR-973": "GF", "US-PR": "PR"}) == {"GF"}


def _tree(tmp: Path, *, land=None, eez=None, floor=None) -> Path:
    iso_dir = tmp / "iso"
    c = Countries(land={"FR": land or box(0, 40, 10, 50), "GF": box(-55, 2, -51, 6), "IN": box(70, 10, 90, 30)},
                  eez={"FR": eez or box(-5, 35, 15, 55), "GF": box(-60, 0, -45, 10), "IN": box(60, 0, 95, 35)},
                  rows={"FR": ["France (Union EEZ and country)"], "GF": ["French Guiana (x)"], "IN": ["India (x)"]})
    fitted = {"IN-DH": FitResult(box(72, 20, 73, 21), _cand(box(72, 20, 73, 21)), "land")}
    labels = {"FR": "France", "GF": "French Guiana", "IN": "India", "FR-GF": "Guyane", "IN-DD": "Daman and Diu",
              "IN-DH": "Dadra", "XZ": "International waters"}
    write.write_tree(iso_dir, c, fitted, labels, {"FR-GF": "GF", "IN-DD": "IN-DH"}, Config("4326", 0.005, 6))
    return iso_dir


def test_write_tree_symlinks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iso_dir = _tree(Path(tmp))
        land, eez = iso_dir / "features", iso_dir / "eez" / "features"
        import os
        assert (land / "FR-GF.geojson").is_symlink() and os.readlink(land / "FR-GF.geojson") == "GF.geojson"
        assert (eez / "FR-GF.geojson").is_symlink() and (eez / "FR-GF.geojson").resolve().is_file()
        assert (land / "IN-DD.geojson").is_symlink() and not (eez / "IN-DD.geojson").exists()
        assert not (land / "XZ.geojson").exists() and "XZ\tInternational waters" in (iso_dir / "labels.tsv").read_text()
        header = (iso_dir / "sources.tsv").read_text().splitlines()[0]
        assert header == "id\tresolution\tupstream\ttarget\tfit\tnote", header
        feature = json.loads((land / "IN-DH.geojson").read_text())
        assert feature["properties"] == {"iso_id": "IN-DH", "iso_name": "Dadra", "name": "Dadra", "source": "src-osm"}


def test_new_id_takes_source_label() -> None:
    fitted = {"DZ-59": FitResult(box(0, 0, 1, 1), _cand(box(0, 0, 1, 1), label="Aflou"), "land"),
              "DE-BE": FitResult(box(0, 0, 1, 1), _cand(box(0, 0, 1, 1), label="Land Berlin"), "land")}
    labels = write.settle_labels({"DZ-59": "DZ-59", "DE-BE": "Berlin"}, {"DE-BE": "Berlin"}, fitted)
    assert labels == {"DZ-59": "Aflou", "DE-BE": "Berlin"}, labels


def test_check_tree_finds_lost_floor_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iso_dir = _tree(Path(tmp))
        assert checks.check_tree(iso_dir) == [], checks.check_tree(iso_dir)
        problems = checks.check_tree(iso_dir, {"FR", "ZZ-9"})
    assert len(problems) == 1 and "ZZ-9" in problems[0], problems


def test_check_tree_finds_land_outside_eez() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iso_dir = _tree(Path(tmp), land=box(0, 40, 10, 50), eez=box(0, 40, 5, 50))
        problems = checks.check_tree(iso_dir)
    assert any("FR" in p and "outside" in p for p in problems), problems


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"OK {test.__name__}")
    print(f"{len(tests)} tests passed")
