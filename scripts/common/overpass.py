"""Overpass API helper with caching, retry/backoff, and provenance.

Same shape as common/wikidata.sparql_csv: one cached file per query, one
SourceRecord per cached file, no surprises on rebuild.

Overpass servers (geofabrik, kumi, etc.) periodically throttle or return a
runtime "too busy" HTML page instead of JSON; this helper retries with
exponential backoff and accepts the response only when it parses as JSON
with non-empty `elements`. On final failure the cache file is left in
place (or absent) so a later rerun can retry without losing prior caches.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .download import SourceRecord

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]
USER_AGENT = (
    "col-gazetteers/1.0 "
    "(https://github.com/CatalogueOfLife/col-gazetteers; m.doering@mac.com)"
)


def fetch(
    query: str,
    cache_path: Path,
    *,
    role: str,
    name: str,
    force: bool = False,
    timeout: int = 300,
    max_attempts: int = 6,
) -> tuple[Path, SourceRecord]:
    """Fetch `query` from Overpass with caching + retry. Returns (path, SourceRecord).

    Rotates across endpoints and backs off exponentially (4s, 8s, 16s, …).
    Raises RuntimeError after `max_attempts` fail; caller can catch and
    fall back to a placeholder.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and not force:
        downloaded_at = datetime.fromtimestamp(
            cache_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds")
    else:
        last_err = None
        delay = 4
        for attempt in range(1, max_attempts + 1):
            endpoint = ENDPOINTS[(attempt - 1) % len(ENDPOINTS)]
            try:
                r = requests.post(
                    endpoint,
                    data={"data": query},
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    timeout=timeout,
                )
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                elif b'<html' in r.content[:512].lower() or b'<!DOCTYPE' in r.content[:512]:
                    last_err = "server returned HTML (overloaded)"
                else:
                    try:
                        obj = r.json()
                    except ValueError as e:
                        last_err = f"JSON parse failed: {e}"
                    else:
                        # Accept even empty `elements` (a country with 0 admin
                        # boundaries tagged with ISO3166-2 is a valid answer,
                        # just unhelpful).
                        if isinstance(obj, dict) and "elements" in obj:
                            cache_path.write_bytes(r.content)
                            downloaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                            last_err = None
                            break
                        last_err = "JSON missing 'elements'"
            except requests.RequestException as e:
                last_err = str(e)
            if attempt < max_attempts:
                time.sleep(delay)
                delay = min(delay * 2, 120)
        if last_err is not None:
            raise RuntimeError(
                f"Overpass fetch failed after {max_attempts} attempts: {last_err}"
            )

    raw = cache_path.read_bytes()
    md5 = hashlib.md5(raw).hexdigest()
    sha256 = hashlib.sha256(raw).hexdigest()
    query_sha = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]

    record = SourceRecord(
        role=role,
        name=name,
        url=ENDPOINTS[0],
        filename=cache_path.name,
        downloaded_at=downloaded_at,
        size_bytes=len(raw),
        md5=md5,
        sha256=sha256,
        upstream_version=f"overpass-sha={query_sha}",
    )
    return cache_path, record
