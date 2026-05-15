"""Cached HTTP download with provenance tracking."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests


@dataclass(frozen=True)
class SourceRecord:
    """Provenance for a single downloaded artifact. Goes into build.json."""

    role: str  # e.g. "shapefile", "labels-api", "names-csv"
    name: str  # human-readable label, e.g. "World_Seas_IHO_v3"
    url: str
    filename: str
    downloaded_at: str  # ISO-8601 UTC
    size_bytes: int
    md5: str
    sha256: str
    upstream_version: str = ""

    def to_json(self) -> dict:
        out = {
            "role": self.role,
            "name": self.name,
            "url": self.url,
            "filename": self.filename,
            "downloaded_at": self.downloaded_at,
            "size_bytes": self.size_bytes,
            "md5": self.md5,
            "sha256": self.sha256,
        }
        if self.upstream_version:
            out["upstream_version"] = self.upstream_version
        return out


def _hashes(path: Path) -> tuple[str, str, int]:
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            md5.update(chunk)
            sha256.update(chunk)
            size += len(chunk)
    return md5.hexdigest(), sha256.hexdigest(), size


def download(
    url: str,
    dest_dir: Path,
    *,
    role: str,
    name: str,
    filename: str | None = None,
    force: bool = False,
    upstream_version: str = "",
    timeout: int = 120,
) -> tuple[Path, SourceRecord]:
    """Download `url` into `dest_dir/filename` unless already present.

    Returns the local path and a SourceRecord with hashes/size/timestamp.
    On cache hit, hashes are computed from the existing file and `downloaded_at`
    reflects the cached file's mtime (UTC).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = url.rsplit("/", 1)[-1] or "download.bin"
    path = dest_dir / filename

    if path.exists() and not force:
        downloaded_at = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds")
    else:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
        downloaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    md5, sha256, size = _hashes(path)
    record = SourceRecord(
        role=role,
        name=name,
        url=url,
        filename=filename,
        downloaded_at=downloaded_at,
        size_bytes=size,
        md5=md5,
        sha256=sha256,
        upstream_version=upstream_version,
    )
    return path, record
