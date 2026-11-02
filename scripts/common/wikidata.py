"""Wikidata SPARQL helper with caching + provenance.

Each call to `sparql_csv` POSTs a query to query.wikidata.org, caches the
CSV result under `sources/<prefix>/`, and returns a `SourceRecord` that
slots into the build manifest the same way a downloaded shapefile would.

The cache key is the destination filename — re-running the build reuses
the cached CSV unless `force=True`. We also stamp the query's SHA-256
into `upstream_version` so a future query rewrite is visible in
`build.json` even if the cache file was left from an earlier query.

The query service throttles by IP, and a GitHub runner shares its IP with
other jobs, so a weekly rebuild got a 429 on its very first query. 429 and
5xx answers and dropped connections are retried, waiting as long as a 429's
Retry-After asks, which the usage policy wants clients to honour.
https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual#Query_limits
"""

from __future__ import annotations

import hashlib
import time
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
ATTEMPTS = 4
RETRY_DELAY = 15  # seconds, doubled each retry
MAX_RETRY_AFTER = 300  # seconds, the most we wait when a 429 asks for longer
RETRY_STATUS = {429, 500, 502, 503, 504}
TRANSIENT = (requests.ConnectionError, requests.Timeout)

_session = requests.Session()


def _retry_after(r) -> float | None:
    """The wait a response asks for in seconds, or None. Wikidata sends
    seconds; an HTTP-date is ignored and the usual backoff used instead."""
    try:
        return min(float(r.headers.get("Retry-After", "")), MAX_RETRY_AFTER)
    except ValueError:
        return None


def _post(query: str, timeout: int) -> bytes:
    """POST `query` and return the CSV, retrying what can pass."""
    delay = RETRY_DELAY
    for attempt in range(1, ATTEMPTS + 1):
        try:
            r = _session.post(
                ENDPOINT,
                data={"query": query},
                headers={"User-Agent": USER_AGENT, "Accept": "text/csv"},
                timeout=timeout,
            )
        except TRANSIENT:
            if attempt == ATTEMPTS:
                raise
            wait, why = delay, "connection failed"
        else:
            if r.status_code not in RETRY_STATUS or attempt == ATTEMPTS:
                r.raise_for_status()
                return r.content
            wait, why = _retry_after(r) or delay, f"HTTP {r.status_code}"
        print(f"[wikidata] attempt {attempt} {why}, retrying in {wait:g}s", flush=True)
        time.sleep(wait)
        delay *= 2
    raise AssertionError("unreachable")


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
        cache_path.write_bytes(_post(query, timeout))
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
