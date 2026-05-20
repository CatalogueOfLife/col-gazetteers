"""Wikidata SPARQL helper with caching + provenance.

Each call to `sparql_csv` POSTs a query to query.wikidata.org, caches the
CSV result under `sources/<prefix>/`, and returns a `SourceRecord` that
slots into the build manifest the same way a downloaded shapefile would.

The cache key is the destination filename — re-running the build reuses
the cached CSV unless `force=True`. We also stamp the query's SHA-256
into `upstream_version` so a future query rewrite is visible in
`build.json` even if the cache file was left from an earlier query.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests

from .download import SourceRecord

ENDPOINT = "https://query.wikidata.org/sparql"
# Wikidata blocks generic requests user-agents; the policy asks for a contact.
# https://meta.wikimedia.org/wiki/User-Agent_policy
USER_AGENT = (
    "col-gazetteers/1.0 "
    "(https://github.com/CatalogueOfLife/col-gazetteers; m.doering@mac.com)"
)


def sparql_csv(
    query: str,
    cache_path: Path,
    *,
    role: str,
    name: str,
    force: bool = False,
    timeout: int = 120,
) -> tuple[Path, SourceRecord]:
    """Run `query` against Wikidata, cache the CSV at `cache_path`, return
    (path, SourceRecord). Reuses the cached file unless `force=True`."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and not force:
        downloaded_at = datetime.fromtimestamp(
            cache_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds")
    else:
        r = requests.post(
            ENDPOINT,
            data={"query": query},
            headers={"User-Agent": USER_AGENT, "Accept": "text/csv"},
            timeout=timeout,
        )
        r.raise_for_status()
        cache_path.write_bytes(r.content)
        downloaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    raw = cache_path.read_bytes()
    md5 = hashlib.md5(raw).hexdigest()
    sha256 = hashlib.sha256(raw).hexdigest()
    query_sha = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]

    record = SourceRecord(
        role=role,
        name=name,
        url=ENDPOINT,
        filename=cache_path.name,
        downloaded_at=downloaded_at,
        size_bytes=len(raw),
        md5=md5,
        sha256=sha256,
        upstream_version=f"sparql-sha={query_sha}",
    )
    return cache_path, record
