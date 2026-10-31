"""MarineRegions REST helpers with on-disk JSON caching.

Four resources are cached under `work/mrgid/api/`:

  records/<shard>/<MRGID>.json            — one record, `{}` if the id is unused
  types.json                              — `getGazetteerTypes`, list[dict]
  by_type/<safe-type-name>/<offset>.json  — one paginated page of records
  wms/<shard>/<MRGID>.json                — per-MRGID WMS pointer list

Records are read one by one. A page of `getGazetteerRecordsByType` takes about
23s for 100 records and a type is paged in sequence, so all 899 pages would take
close to 6 hours, against 2 hours for all ids at the rate the server allows.

marineregions.org answers 429 after about 180 requests in a burst and holds
10 requests a second, so every request is paced to that.

The WMS pointer list returned by `getGazetteerWMSes` tells us, for one MRGID:
which GeoServer layer holds its polygon and which attribute column to filter
on. Pointers vary even within a single placeType (e.g. some IHO sea areas key
on `mrgid`, others on `id`), so we resolve them per record rather than
hard-coding a placeType→layer table.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from urllib.parse import quote

import requests

REST_BASE = "https://marineregions.org/rest"
WFS_BASE = "https://geo.vliz.be/geoserver"
PAGE_SIZE = 100
HTTP_TIMEOUT = 60
RETRIES = 4              # total attempts on transient failures
RETRY_BACKOFF = 2.0      # seconds, doubled each retry
RATE_LIMITED_WAIT = 30.0 # seconds to wait after a 429
REQUEST_INTERVAL = 0.1   # seconds between requests, over all threads
# Ids are not contiguous, the largest gap so far is 228. The walk ends after a
# whole block without any record.
ID_BLOCK = 2000


_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=32))
_pace_lock = threading.Lock()
_next_request = 0.0


def _pace() -> None:
    """Block until this thread's turn, so that all threads together keep the rate."""
    global _next_request
    with _pace_lock:
        now = time.monotonic()
        wait = _next_request - now
        _next_request = max(now, _next_request) + REQUEST_INTERVAL
    if wait > 0:
        time.sleep(wait)


def _get_with_retry(url: str, *, timeout: int = HTTP_TIMEOUT):
    """GET with backoff on connection / read timeouts, 429 and 5xx.

    Returns the final Response (which may still be a 404 — that's not a
    "transient" failure and the caller handles it). Raises after RETRIES
    consecutive transient failures.
    """
    delay = RETRY_BACKOFF
    last_exc: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            _pace()
            r = _session.get(url, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                last_exc = requests.HTTPError(f"{r.status_code} on {url}")
                if r.status_code == 429 and attempt < RETRIES:
                    time.sleep(RATE_LIMITED_WAIT)
                    continue
            else:
                return r
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
        if attempt < RETRIES:
            time.sleep(delay)
            delay *= 2
    assert last_exc is not None
    raise last_exc


@dataclass(frozen=True)
class GazetteerRecord:
    """One row of the gazetteer (REST `getGazetteerRecordsByType` output)."""

    mrgid: int
    name: str
    place_type: str
    source: str
    latitude: float | None
    longitude: float | None
    min_lat: float | None
    min_lon: float | None
    max_lat: float | None
    max_lon: float | None

    @classmethod
    def from_api(cls, d: dict) -> "GazetteerRecord":
        return cls(
            mrgid=int(d["MRGID"]),
            name=(d.get("preferredGazetteerName") or "").strip(),
            place_type=(d.get("placeType") or "").strip(),
            source=(d.get("gazetteerSource") or "").strip(),
            latitude=d.get("latitude"),
            longitude=d.get("longitude"),
            min_lat=d.get("minLatitude"),
            min_lon=d.get("minLongitude"),
            max_lat=d.get("maxLatitude"),
            max_lon=d.get("maxLongitude"),
        )


@dataclass(frozen=True)
class WMSPointer:
    """One (layer, attribute, value) tuple pointing at an MRGID's geometry."""

    mrgid: int
    namespace: str        # e.g. "MarineRegions", "World"
    feature_type: str     # e.g. "eez", "world_quadrants_20150805"
    attribute: str        # WFS column to filter on, e.g. "mrgid", "mrgid_2", "id"
    value: str            # filter value as string

    @property
    def layer(self) -> str:
        return f"{self.namespace}:{self.feature_type}"


# ---------- caching primitives ----------

def _cache_read(path: Path):
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _cache_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(path)


def _safe(name: str) -> str:
    """Sanitize a placeType name for use as a directory name."""
    return name.replace("/", "_").replace(" ", "_").replace("(", "").replace(")", "")


# ---------- REST endpoints ----------

def get_place_types(cache_dir: Path) -> list[str]:
    """Return the gazetteer's placeType *names*. Order is API-defined."""
    cached = _cache_read(cache_dir / "types.json")
    if cached is None:
        url = f"{REST_BASE}/getGazetteerTypes.json/"
        r = _get_with_retry(url)
        r.raise_for_status()
        cached = r.json()
        _cache_write(cache_dir / "types.json", cached)
    return [t["type"] for t in cached if t.get("type")]


def fetch_records_by_type(type_name: str, cache_dir: Path) -> list[GazetteerRecord]:
    """Paginate `getGazetteerRecordsByType` for one placeType.

    Each page (100 records) is cached individually so an interrupted run
    resumes mid-type. A short page (< PAGE_SIZE) or empty page ends pagination.
    """
    type_dir = cache_dir / "by_type" / _safe(type_name)
    out: list[GazetteerRecord] = []
    offset = 0
    while True:
        page_path = type_dir / f"{offset:06d}.json"
        cached = _cache_read(page_path)
        if cached is None:
            url = f"{REST_BASE}/getGazetteerRecordsByType.json/{quote(type_name)}/{offset}/"
            r = _get_with_retry(url)
            if r.status_code == 404:
                cached = []
            else:
                r.raise_for_status()
                cached = r.json()
            _cache_write(page_path, cached)
        for d in cached:
            try:
                out.append(GazetteerRecord.from_api(d))
            except (KeyError, ValueError, TypeError):
                # Skip records missing MRGID or with non-numeric IDs
                continue
        if len(cached) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return out


def fetch_record(mrgid: int, cache_dir: Path) -> GazetteerRecord | None:
    """One record by id, or None if the id isn't in use or the record was deleted."""
    path = cache_dir / "records" / f"{mrgid // 1000:04d}" / f"{mrgid}.json"
    cached = _cache_read(path)
    if cached is None:
        r = _get_with_retry(f"{REST_BASE}/getGazetteerRecordByMRGID.json/{mrgid}/")
        if r.status_code == 404:
            cached = {}
        else:
            r.raise_for_status()
            cached = r.json()
        _cache_write(path, cached)
    if not cached or cached.get("status") == "deleted":
        return None
    return GazetteerRecord.from_api(cached)


def fetch_all_records(
    cache_dir: Path,
    *,
    workers: int = 16,
    on_progress=None,
) -> dict[int, GazetteerRecord]:
    """Every record of the gazetteer, by walking the ids until they run out.

    A failed lookup raises. A build that goes on without it would drop the
    record from the gazetteer.
    """
    records: dict[int, GazetteerRecord] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in count(1, ID_BLOCK):
            block = range(start, start + ID_BLOCK)
            found = [r for r in pool.map(lambda m: fetch_record(m, cache_dir), block) if r]
            if not found:
                return records
            records.update((r.mrgid, r) for r in found)
            if on_progress:
                on_progress(block[-1], len(records))
    raise AssertionError("unreachable")


def fetch_wms_pointers(mrgid: int, cache_dir: Path) -> list[WMSPointer]:
    """Resolve all (layer, attribute, value) geometry pointers for one MRGID.

    Returns an empty list when the MRGID has no associated geometry layer.
    Cache shards by `mrgid // 1000` to avoid a single directory of 100k files.
    """
    shard = mrgid // 1000
    path = cache_dir / "wms" / f"{shard:04d}" / f"{mrgid}.json"
    cached = _cache_read(path)
    if cached is None:
        url = f"{REST_BASE}/getGazetteerWMSes.json/{mrgid}/"
        r = _get_with_retry(url)
        if r.status_code == 404:
            cached = []
        else:
            r.raise_for_status()
            try:
                cached = r.json()
            except json.JSONDecodeError:
                cached = []
        _cache_write(path, cached)
    return [
        WMSPointer(
            mrgid=int(d["MRGID"]),
            namespace=str(d["namespace"]),
            feature_type=str(d["featureType"]),
            attribute=str(d["featureName"]),
            value=str(d["value"]),
        )
        for d in cached
        if d.get("namespace") and d.get("featureType") and d.get("featureName")
    ]


def fetch_wms_pointers_parallel(
    mrgids: list[int],
    cache_dir: Path,
    *,
    workers: int = 16,
    on_progress=None,
) -> dict[int, list[WMSPointer]]:
    """Parallel-fetch WMS pointers.

    Raises if any lookup failed, as the MRGID would silently lose its polygon
    and become a point. Everything else is cached, so re-running is quick.
    """
    out: dict[int, list[WMSPointer]] = {}
    failed: list[int] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_wms_pointers, m, cache_dir): m for m in mrgids}
        done = 0
        for fut in as_completed(futures):
            mrgid = futures[fut]
            try:
                out[mrgid] = fut.result()
            except Exception:
                failed.append(mrgid)
            done += 1
            if on_progress and (done % 500 == 0 or done == len(mrgids)):
                on_progress(done, len(mrgids))
    if failed:
        raise RuntimeError(f"{len(failed)} WMS pointer lookups failed, e.g. MRGID {sorted(failed)[:5]}")
    return out


def wfs_geojson_url(
    namespace: str,
    feature_type: str,
    cql_filter: str | None = None,
) -> str:
    """Build the WFS GetFeature URL for one layer, returning GeoJSON.

    When `cql_filter` is provided, the server only returns rows matching it —
    essential for huge layers (e.g. World:worldgazetteer has 150k features
    of which we may want a handful).
    """
    url = (
        f"{WFS_BASE}/{namespace}/ows"
        f"?service=WFS&version=2.0.0&request=GetFeature"
        f"&typeNames={namespace}:{feature_type}"
        f"&outputFormat=application/json"
    )
    if cql_filter:
        url += f"&CQL_FILTER={quote(cql_filter)}"
    return url


def build_cql_filter(pointers: list["WMSPointer"]) -> str:
    """Build a CQL filter matching all (attribute, value) pairs in `pointers`.

    Groups by attribute: `attr1 IN (v1,v2,...) OR attr2 IN (...)`. Numeric
    values are unquoted; anything that doesn't parse as an int/float is
    quoted as a CQL string literal (single quotes, doubling embedded single
    quotes). Many gazetteer layers key on string codes (`name`, `nuts_id`,
    `vcname`, …) so unquoted-only filters would 400 on the server.
    """
    from collections import defaultdict
    by_attr: dict[str, set[str]] = defaultdict(set)
    for p in pointers:
        by_attr[p.attribute].add(p.value)
    parts: list[str] = []
    for attr in sorted(by_attr):
        vlist = ",".join(
            _cql_literal(v)
            for v in sorted(by_attr[attr], key=lambda v: (len(v), v))
        )
        parts.append(f"{attr} IN ({vlist})")
    return " OR ".join(parts)


def _cql_literal(value: str) -> str:
    """Emit a CQL literal: bare for numeric, single-quoted for everything else."""
    # Tabs and other control chars sometimes leak into upstream value strings;
    # strip them — they're never part of the legitimate identifier.
    v = value.strip().replace("\t", "")
    try:
        # int first, then float — matches CQL numeric literal semantics
        int(v)
        return v
    except ValueError:
        pass
    try:
        float(v)
        return v
    except ValueError:
        pass
    return "'" + v.replace("'", "''") + "'"
