"""Build the `wdpa` gazetteer — World Database on Protected Areas (labels only).

Source: Protected Planet's monthly global WDPA + WDOECM release, in its
attribute-only CSV form (`WDPA_<Mon><Year>_Public_csv.zip`), downloaded from
UNEP-WCMC's CloudFront mirror.

**This gazetteer ships labels only — no geometries.** The WDPA Terms &
Conditions prohibit redistributing the data to third parties, which committing
the polygons to this public repo (and serving them via the CLB
`/vocab/area` GeoJSON endpoint) would violate. We therefore redistribute only
the `SITE_ID → NAME_ENG` mapping (with attribution); for `wdpa:{id}` the backend
resolves the name and links out to https://www.protectedplanet.net/<SITE_ID>.

Using the attribute-only CSV (~24 MB) instead of the multi-GB geodatabase means
the build never even downloads the shapes it cannot redistribute, and needs no
GDAL — pure stdlib `csv`.

CSV schema (2026 release): the id is `SITE_ID` (integer; the WDPAID) and the
label is `NAME_ENG` (English name). `SITE_PID` is the parcel id and `NAME` the
local-language name (both unused). A single SITE_ID appears in multiple parcel
rows; we collapse to one label per SITE_ID. The combined CSV carries both
`TYPE=Polygon` and `TYPE=Point` records.

The backend's `Gazetteer.WDPA` enum entry (pattern `^[0-9]+$`) is already
deployed; no backend work is required.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR
from common.download import download
from common.ids import assert_unique
from common.labels import write_labels
from common.manifest import write_manifest

PREFIX = "wdpa"
SOURCE_BASE = "https://d1gam3xoknrgr2.cloudfront.net/current"
SOURCE_NAME = "World Database on Protected Areas (WDPA + WDOECM), Protected Planet"

ID_FIELD = "SITE_ID"
NAME_FIELD = "NAME_ENG"
ID_RE = re.compile(r"^[0-9]+$")  # mirrors the backend Gazetteer.WDPA pattern


def current_month_token() -> str:
    """Protected Planet's release token, e.g. `Jun2026` (`%b%Y`, en-US month)."""
    return datetime.now(timezone.utc).strftime("%b%Y")


def read_labels_from_csv(csv_path: Path) -> list[tuple[str, str]]:
    """One (SITE_ID, NAME_ENG) row per distinct SITE_ID; first name wins.

    Skips rows with a missing/empty id or name, and asserts every id matches
    the backend's `^[0-9]+$` pattern so a malformed source row fails loudly.
    """
    seen: dict[str, str] = {}
    skipped = 0
    # WDPA CSVs are UTF-8 with a BOM; utf-8-sig strips it from the header.
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = {ID_FIELD, NAME_FIELD} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"CSV missing expected column(s) {sorted(missing)}; "
                f"got {reader.fieldnames}"
            )
        for row in reader:
            site_id = (row.get(ID_FIELD) or "").strip()
            name = (row.get(NAME_FIELD) or "").strip()
            if not site_id or not name:
                skipped += 1
                continue
            if not ID_RE.match(site_id):
                raise ValueError(f"SITE_ID {site_id!r} does not match /^[0-9]+$/")
            seen.setdefault(site_id, name)
    if skipped:
        print(f"[{PREFIX}] skipped {skipped} row(s) with empty id/name")
    return list(seen.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--month",
        default=current_month_token(),
        help="Release token, e.g. Jun2026 (default: current UTC month)",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    token = args.month
    filename = f"WDPA_{token}_Public_csv.zip"
    url = f"{SOURCE_BASE}/{filename}"
    out_dir = REPO_ROOT / PREFIX

    print(f"[{PREFIX}] labels-only build (no geometries — WDPA license)")
    zip_path, source_record = download(
        url,
        SOURCES_DIR / PREFIX,
        role="csv",
        name=SOURCE_NAME,
        filename=filename,
        force=args.force,
        upstream_version=token,
    )
    print(f"[{PREFIX}] source: {zip_path} ({source_record.size_bytes:,} bytes, md5={source_record.md5[:8]}…)")

    csv_name = f"WDPA_{token}_Public_csv.csv"
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if csv_name not in names:
            # Fall back to the single top-level *_Public_csv.csv if the token-based
            # name ever drifts from the archive's internal naming.
            candidates = [
                n for n in names
                if n.endswith("_Public_csv.csv") and "/" not in n
            ]
            if len(candidates) != 1:
                raise ValueError(
                    f"could not locate the main CSV in {filename}; "
                    f"expected {csv_name!r}, archive has {candidates or names[:5]}"
                )
            csv_name = candidates[0]
        extract_dir = SOURCES_DIR / PREFIX
        zf.extract(csv_name, extract_dir)
        csv_path = extract_dir / csv_name
    print(f"[{PREFIX}] extracted {csv_name}")

    rows = read_labels_from_csv(csv_path)
    assert_unique([r[0] for r in rows])
    label_count = write_labels(out_dir / "labels.tsv", rows)
    print(f"[{PREFIX}] {label_count} labels (one per SITE_ID)")

    write_manifest(
        out_dir / "build.json",
        prefix=PREFIX,
        sources=[source_record],
        feature_count=0,
        label_count=label_count,
    )
    print(f"[{PREFIX}] manifest → {out_dir / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
