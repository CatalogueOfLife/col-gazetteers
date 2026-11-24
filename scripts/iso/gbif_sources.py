"""Find the two files GBIF imports for its country areas.

GBIF mirrors VLIZ's EEZ-land union and EEZ v12 on a server that answers only
GBIF's own network. VLIZ asks that their files are not offered for download
elsewhere, so they are never published from a public place. The build looks:

1. in `sources/iso/`, where someone on GBIF's VPN, or the CI workflow from the
   private release, puts them;
2. on GBIF's mirror, when the build runs on GBIF's network.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from common.download import SourceRecord, download

MIRROR = "https://download.gbif.org/MapDataMirror/2026/10/"
UNION_ZIP = "EEZ_land_union_v4_202410.zip"
EEZ_GPKG = "eez_v12.gpkg"
RELEASE_REPO = "CatalogueOfLife/gazetteer-sources"
RELEASE_TAG = "gbif-mapdatamirror-2026-10"

# filename, build.json role, name, upstream version
_FILES = (
    (UNION_ZIP, "gbif-eez-land-union", "VLIZ EEZ-land union v4, as mirrored by GBIF", "v4 2024-10"),
    (EEZ_GPKG, "gbif-eez-v12", "VLIZ EEZ v12, as mirrored by GBIF", "v12"),
)


def _local_record(path: Path, role: str, name: str, version: str) -> SourceRecord:
    data = path.read_bytes()
    return SourceRecord(
        role=role,
        name=name,
        url=MIRROR + path.name,
        filename=path.name,
        downloaded_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
        size_bytes=len(data),
        md5=hashlib.md5(data).hexdigest(),
        sha256=hashlib.sha256(data).hexdigest(),
        upstream_version=version,
    )


def locate_gbif_files(sources_dir: Path, *, force: bool = False) -> tuple[Path, Path, list[SourceRecord]]:
    """(union zip, EEZ gpkg, their source records). A local copy wins unless `force` asks for the mirror."""
    sources_dir.mkdir(parents=True, exist_ok=True)
    paths, records = [], []
    for filename, role, name, version in _FILES:
        local = sources_dir / filename
        if local.is_file() and not force:
            records.append(_local_record(local, role, name, version))
            paths.append(local)
            continue
        try:
            path, record = download(
                MIRROR + filename, sources_dir, role=role, name=name, filename=filename,
                force=force, upstream_version=version, timeout=600,
            )
        except Exception as e:
            if local.is_file():
                print(f"[iso] GBIF's mirror refused {filename} ({e}), using the local copy")
                records.append(_local_record(local, role, name, version))
                paths.append(local)
                continue
            raise RuntimeError(
                f"{filename} is not in {sources_dir} and GBIF's mirror refused it ({e}). "
                f"The mirror answers only GBIF's network. Elsewhere, with access to the private repo: "
                f"gh release download {RELEASE_TAG} -R {RELEASE_REPO} -D {sources_dir} "
                f"-p {UNION_ZIP} -p {EEZ_GPKG}"
            ) from e
        records.append(record)
        paths.append(path)
    return paths[0], paths[1], records
