"""Tests for the iso build on small fixtures, no downloads or data needed.

Run with `python scripts/test_iso.py` (no test framework required, like
test_wikidata.py). Exit code 0 on success, an assertion error otherwise.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from iso import gbif_drift, gbif_sources  # noqa: E402


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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"OK {test.__name__}")
    print(f"{len(tests)} tests passed")
