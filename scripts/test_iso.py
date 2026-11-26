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
from iso import overture  # noqa: E402
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
    assert records[1].sha256 == hashlib.sha256(b"gpkg").hexdigest(), records[1]


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


def test_missing_targets_of_gbif_changes() -> None:
    every = [_row(m, "XXX", box(0, 0, 1, 1)) for m in political.CHANGED_MRGIDS]
    osm = {rel: box(0, 0, 1, 1) for rel in political.GBIF_OSM_RELATIONS}
    assert political.missing_targets(every, osm) == []
    missing = political.missing_targets([r for r in every if r["mrgid_eez"] != 8453], {})
    assert "mrgid_eez 8453" in missing and "OSM relation 913110" in missing, missing
    as_text = [{**r, "mrgid_eez": str(r["mrgid_eez"])} for r in every]
    assert len(political.missing_targets(as_text, osm)) == len(political.CHANGED_MRGIDS)


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


def _raises(fn, *args) -> str:
    try:
        fn(*args)
    except RuntimeError as e:
        return str(e)
    raise AssertionError(f"{fn.__name__} accepted it")


def test_wikidata_too_few_codes_or_values_fails() -> None:
    def code(i, **kw):
        return universe.WdCode(**{"code": f"XX-{i}", "item": f"Q{i}", "label": f"r{i}", "alpha2": "", "replaced_by": (),
                                  "osm_relation": i, "lon": 1.0, "lat": 1.0, "ended": False, **kw})
    universe.check_wikidata({f"XX-{i}": code(i) for i in range(5000)})
    assert "4999 codes" in _raises(universe.check_wikidata, {f"XX-{i}": code(i) for i in range(4999)})
    no_coords = {f"XX-{i}": code(i, lon=None, lat=None) if i % 5 == 0 else code(i) for i in range(5000)}
    assert "coordinates" in _raises(universe.check_wikidata, no_coords)


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


def test_reassigned_moroccan_regions_take_their_current_names() -> None:
    # ISO 3166-2:MA gave MA-01..12 to the 12 regions of 2015; the floor still names the old ones.
    labels = universe.build_universe({"MA-08": "Grand Casablanca", "MA-13": "Suss-Massa-Draa"}, {})
    assert labels["MA-08"] == "Drâa-Tafilalet", labels
    assert labels["MA-13"] == "Suss-Massa-Draa", "the dropped old codes keep their old regions"


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
    wd = {"FR-GF": _wd("FR-GF", alpha2="GF", replaced_by=["FR-973"]),
          "IN-DD": _wd("IN-DD", replaced_by=["IN-DH"], ended=True)}
    aliases = universe.aliases_for(labels, wd)
    assert aliases == {"FR-GF": "GF", "TP": "TL"}, "superseded codes are decided once shapes are known"


def test_superseded_only_one_to_one() -> None:
    wd = {"IN-DD": _wd("IN-DD", replaced_by=["IN-DH"], ended=True),
          "CU-02": _wd("CU-02", replaced_by=["CU-15", "CU-16"], ended=True)}
    aliases = universe.superseded(wd, {"IN-DD", "IN-DH", "CU-02", "CU-15", "CU-16"})
    assert aliases == {"IN-DD": "IN-DH"}, aliases


def test_superseded_needs_a_withdrawn_code() -> None:
    # NO-31 Østfold has a P1366 to NO-30 Viken, but was reinstated in 2024: it is current.
    wd = {"NO-31": _wd("NO-31", replaced_by=["NO-30"]), "IN-DD": _wd("IN-DD", replaced_by=["IN-DH"], ended=True)}
    assert universe.superseded(wd, {"NO-31", "NO-30", "IN-DD", "IN-DH"}) == {"IN-DD": "IN-DH"}


def test_superseded_skips_ids_with_a_shape() -> None:
    wd = {"FR-B": _wd("FR-B", replaced_by=["FR-NAQ"], ended=True)}
    assert universe.superseded(wd, {"FR-B", "FR-NAQ"}, shaped={"FR-B"}) == {}


def test_parse_keeps_a_code_current_while_one_item_is() -> None:
    # MA-09 is on an ended item (Chaouia-Ouardigha) and a current one.
    text = (
        "code,item,itemLabel,alpha2,replacedByCode,osm,coord,end\n"
        "MA-09,http://www.wikidata.org/entity/Q1,Chaouia-Ouardigha,,MA-06,,,2015-09-10T00:00:00Z\n"
        "MA-09,http://www.wikidata.org/entity/Q2,Béni Mellal-Khénifra,,,,,\n"
    )
    wd = universe.parse_wikidata_csv(text)
    assert not wd["MA-09"].ended, wd["MA-09"]
    assert universe.superseded(wd, {"MA-09", "MA-06"}) == {}


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


def _star_across_antimeridian():
    """A 10-pointed star around 180°, valid where it is, but crossing itself as written across ±180."""
    import math
    from shapely.geometry import Polygon
    pts = [(180 + (3 if k % 2 == 0 else 1) * math.cos(math.pi * k / 5),
            65 + (3 if k % 2 == 0 else 1) * math.sin(math.pi * k / 5) / 2) for k in range(10)]
    return Polygon(pts), [(x - 360 if x > 180 else x, y) for x, y in pts] + [pts[0]]


def test_relation_across_antimeridian_is_unwrapped_before_repair() -> None:
    star, wrapped = _star_across_antimeridian()
    geom = osm.to_candidates({"elements": [_relation(6, "XX-E", wrapped)]})["XX-E"].geom
    assert abs(geom.area - star.area) < 1e-6, (geom.area, star.area)
    assert -180 <= geom.bounds[0] and geom.bounds[2] <= 180, geom.bounds


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


def test_snapshot_records_its_countries() -> None:
    doc = {"elements": [_relation(1, "AA-1", _square(0, 0))]}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / osm.SNAPSHOT
        osm.write_snapshot(osm.relation_geoms(doc), path, countries=["AA", "BB"])
        assert osm.snapshot_missing(path, {"AA", "BB", "CC"}) == ["CC"]


def test_runtime_error_remark_is_a_failure() -> None:
    def fake_fetch(query, cache_path, **kwargs):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        remark = ', "remark": "runtime error: Query timed out in \\"query\\" at line 1"' if "(XX)" in query else ""
        cache_path.write_text('{"elements": []' + remark + "}", encoding="utf-8")
        return cache_path, None

    saved = osm.overpass_fetch
    osm.overpass_fetch = fake_fetch
    try:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                osm.fetch_osm(["AA", "XX"], [], Path(tmp), force=False, batch=1)
            except RuntimeError as e:
                assert "XX" in str(e) and "AA" not in str(e), e
                assert not (Path(tmp) / "osm" / "countries_XX.json").exists(), "a truncated answer is not kept"
            else:
                raise AssertionError("a truncated Overpass answer was accepted")
    finally:
        osm.overpass_fetch = saved


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


# --- Overture -----------------------------------------------------------------

def test_overture_latest_release() -> None:
    listing = ("<ListBucketResult><CommonPrefixes><Prefix>release/2026-10-21.0/</Prefix></CommonPrefixes>"
               "<CommonPrefixes><Prefix>release/2026-11-18.0/</Prefix></CommonPrefixes>"
               "<CommonPrefixes><Prefix>release/2026-09-24.1/</Prefix></CommonPrefixes></ListBucketResult>")
    assert overture.latest_release(listing) == "2026-11-18.0"


def test_overture_choose_prefers_code_then_relation_then_wikidata() -> None:
    m = overture.Match
    chosen = overture.choose([
        m("XX-A", "wikidata", "a3", 3, 1, "A3"), m("XX-A", "osm", "a2", 2, 2, "A2"), m("XX-A", "region", "a1", 1, 1, "A1"),
        m("XX-B", "wikidata", "b2", 5, 2, "B2"), m("XX-B", "wikidata", "b1", 4, 1, "B1"),
        m("XX-C", "osm", "c1", 6, None, "C"),
    ])
    assert {code: c.area for code, c in chosen.items()} == {"XX-A": "a1", "XX-B": "b1", "XX-C": "c1"}, chosen


def test_overture_snapshot_items_share_a_relation() -> None:
    m = overture.Match
    chosen = {"XX-A": m("XX-A", "region", "a1", 7, 1, "Alpha"), "XX-B": m("XX-B", "osm", "a1", 7, 1, "Alpha"),
              "XX-C": m("XX-C", "osm", "c1", 8, 2, "Gamma")}
    items = overture.snapshot_items(chosen, {"a1": box(0, 0, 1, 1)})  # c1's shape is missing
    assert [(rel, tags) for rel, tags, _ in items] == [
        (7, {"ISO3166-2": "XX-A;XX-B", "boundary": "administrative", "name": "Alpha"})], items
    cands = osm.candidates_from(items)
    assert set(cands) == {"XX-A", "XX-B"} and cands["XX-A"].source == "osm-relation-7"


def test_overture_snapshot_items_unwrap_antimeridian() -> None:
    from shapely.geometry import Polygon
    chosen = {"XX-A": overture.Match("XX-A", "osm", "a1", 7, 1, "Wide")}
    wrapped = Polygon([(179, 0), (-179, 0), (-179, 1), (179, 1)])
    (_, _, geom), = overture.snapshot_items(chosen, {"a1": wrapped})
    assert abs(geom.area - 2) < 1e-9, geom.area


def test_snapshot_records_its_source() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.geojson.gz"
        osm.write_snapshot([(1, {"ISO3166-2": "XX-A"}, box(0, 0, 1, 1))], path, countries=["XX"],
                           source="overture 2026-11-18.0")
        assert osm.snapshot_source(path) == "overture 2026-11-18.0"
        osm.write_snapshot([(1, {"ISO3166-2": "XX-A"}, box(0, 0, 1, 1))], path, countries=["XX"])
        assert osm.snapshot_source(path) == "overpass"


# --- fallbacks ----------------------------------------------------------------

def test_natural_earth_too_few_codes_fails() -> None:
    cand = Candidate(box(0, 0, 1, 1), "natural-earth-admin-1", "natural-earth", label="x")
    natural_earth.check_natural_earth({f"XX-{i}": cand for i in range(4000)})
    assert "3999 subdivision codes" in _raises(natural_earth.check_natural_earth, {f"XX-{i}": cand for i in range(3999)})


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


class _Response:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_gb_missing_layer_is_skipped_but_outage_fails() -> None:
    saved = geoboundaries.requests.get
    try:
        geoboundaries.requests.get = lambda url, **kw: _Response(404)
        with tempfile.TemporaryDirectory() as tmp:
            cands, _ = geoboundaries.geoboundaries_candidates({"AL-BR": "Berat"}, Path(tmp), iso3={"AL": "ALB"},
                                                              force=False)
        assert cands == {}
        geoboundaries.requests.get = lambda url, **kw: _Response(503)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                geoboundaries.geoboundaries_candidates({"AL-BR": "Berat"}, Path(tmp), iso3={"AL": "ALB"}, force=False)
            except RuntimeError as e:
                assert "503" in str(e), e
            else:
                raise AssertionError("a geoBoundaries outage passed silently")
    finally:
        geoboundaries.requests.get = saved


def test_derive_needs_all_parts() -> None:
    cands = derived.derive({"GB-ENG": box(0, 0, 2, 2), "GB-WLS": box(-1, 0, 0, 1)})
    assert "GB-EAW" in cands and "GB-GBN" not in cands, cands
    assert abs(cands["GB-EAW"].geom.area - 5) < 1e-9 and cands["GB-EAW"].resolution == "derived"
    assert "FR-COR" in derived.DERIVED and derived.DERIVED["FR-H"] == ("FR-2A", "FR-2B")


def test_irish_provinces_are_their_counties() -> None:
    # geoBoundaries dropped the provinces in 2026; every county of the Republic is in exactly one.
    provinces = {p: derived.DERIVED[p] for p in ("IE-C", "IE-L", "IE-M", "IE-U")}
    counties = [c for parts in provinces.values() for c in parts]
    assert len(counties) == 26 and len(set(counties)) == 26, provinces
    assert provinces["IE-U"] == ("IE-CN", "IE-DL", "IE-MN"), "Ulster's counties in the Republic only"
    assert "IE-D" in provinces["IE-L"] and "IE-G" in provinces["IE-C"] and "IE-CO" in provinces["IE-M"]


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
    island = _cand(box(12, 12, 13, 13), "natural-earth")
    circle = _cand(placeholder.circle(5, 5), "placeholder-circle")
    r = fit.fit([island, circle], land, eez, {})
    assert r.mode == "eez" and r.candidate is island, "a real outline beats a placeholder that touches land"
    assert r.absorb, "a Natural Earth island is land"
    c = Countries(land={"AA": land}, eez={"AA": eez})
    fit.absorb(c, ["AA"], r.geom)
    assert abs(c.land["AA"].area - 101) < 1e-9, c.land["AA"].area


def test_eez_pass_prefers_land_like_sources() -> None:
    sea_outline = _cand(box(11, 11, 21, 21))  # an atoll's OSM boundary, mostly sea
    island = _cand(box(12, 12, 12.5, 12.5), "natural-earth")
    r = fit.fit([sea_outline, island], box(0, 0, 10, 10), box(-5, -5, 30, 30), {})
    assert r.mode == "eez" and r.candidate is island and r.absorb, r


def test_sea_outline_and_placeholder_are_not_absorbed() -> None:
    land, eez = box(0, 0, 10, 10), box(-5, -5, 30, 30)
    r = fit.fit([_cand(box(11, 11, 21, 21))], land, eez, {})
    assert r.mode == "eez" and not r.absorb, "an OSM boundary at sea is not land"
    r = fit.fit([_cand(placeholder.circle(20, 20), "placeholder-circle")], land, eez, {})
    assert r.mode == "eez" and not r.absorb, "a placeholder circle at sea is not land"


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


def test_historic_codes_never_hold_a_conflict() -> None:
    geoms = {"YU": box(0, 0, 10, 10), "HR": box(0, 0, 10, 10), "AN": box(20, 0, 30, 10), "FR": box(40, 0, 50, 10)}
    assert set(countries.current(geoms)) == {"HR", "FR"}


def test_absorbed_island_reaches_the_historic_union() -> None:
    c = Countries(land={"CW": box(0, 0, 1, 1), "AN": box(0, 0, 1, 1)},
                  eez={"CW": box(-5, -5, 5, 5), "AN": box(-5, -5, 5, 5)})
    fit.absorb(c, ["CW"], box(3, 3, 4, 4))
    assert abs(c.land["AN"].area - 2) < 1e-9, c.land["AN"].area


def test_placeholders_not_yet_tried_on_geoboundaries() -> None:
    circle = _cand(box(0, 0, 1, 1), "placeholder-circle")
    ne = _cand(box(0, 0, 1, 1), "natural-earth")
    fitted = {"XX-A": FitResult(circle.geom, circle, "land"), "XX-B": FitResult(circle.geom, circle, "land"),
              "XX-C": FitResult(ne.geom, ne, "land")}
    assert fit.untried(fitted, tried={"XX-B"}) == ["XX-A"]


def test_placeholder_kept_however_small() -> None:
    tiny = box(0, 0, 0.01, 0.01)
    r = fit.fit([_cand(placeholder.circle(0, 0), "placeholder-circle")], tiny, tiny, {})
    assert r.mode == "land" and abs(r.geom.area - 0.0001) < 1e-9, r


def test_fit_survives_geos_rectangle_clip_bug() -> None:
    # A piece of Azerbaijan's land on which GEOS's clip_by_rect raises
    # "Invalid number of points in LinearRing found 3" (AZ-XAC, in the first full build).
    import shapely as _shapely
    area = _shapely.from_wkt((Path(__file__).parent / "test_data" / "iso_clip_by_rect_bug.wkt").read_text())
    bounds = (48.46221094477926, 41.34851003670883, 49.0786976138348, 41.84528229400007)
    r = fit.fit([_cand(box(*bounds))], area, area, {})
    assert r.mode == "land" and r.geom.area > 0, r


def test_clip_to_box_survives_geos_rectangle_clip_bug() -> None:
    import shapely as _shapely
    from iso import geom as _geom
    area = _shapely.from_wkt((Path(__file__).parent / "test_data" / "iso_clip_by_rect_bug.wkt").read_text())
    bounds = (48.46221094477926, 41.34851003670883, 49.0786976138348, 41.84528229400007)
    clipped = _geom.clip_to_box(area, *bounds)
    assert clipped.is_valid and abs(clipped.area - area.intersection(box(*bounds)).area) < 1e-9


def test_territories() -> None:
    assert fit.territories("FR", {"FR-GF": "GF", "FR-973": "GF", "US-PR": "PR"}) == {"GF"}
    assert fit.territories("NL", {}) == {"BQ"}, "NL-BQ1..3 lie in BQ, which has no dual code"


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


def test_write_tree_refuses_other_crs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iso_dir = _tree(Path(tmp))
        try:
            write.write_tree(iso_dir, Countries({}, {}, []), {}, {}, {}, Config("3857", 500.0, 0))
        except NotImplementedError as e:
            assert "4326" in str(e)
        else:
            raise AssertionError("wrote an EPSG:3857 tree in degrees")
        assert (iso_dir / "features" / "FR-GF.geojson").is_symlink(), "the tree was touched"


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


def test_write_tree_keeps_eez_maps() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "iso" / "eez").mkdir(parents=True)
        (Path(tmp) / "iso" / "eez" / "map.svg").write_text("<svg/>")
        iso_dir = _tree(Path(tmp))
        assert (iso_dir / "eez" / "map.svg").is_file(), "only eez/features is rewritten"


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


def test_check_tree_tolerates_simplification_slivers() -> None:
    # Monaco-sized: land sticks out of land + EEZ by 0.003°, less than the simplification
    # moves edges, which is 15% of so small a country.
    with tempfile.TemporaryDirectory() as tmp:
        iso_dir = _tree(Path(tmp), land=box(0, 40, 0.02, 40.02), eez=box(0, 40, 0.017, 40.02))
        problems = checks.check_tree(iso_dir)
    assert problems == [], problems


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
