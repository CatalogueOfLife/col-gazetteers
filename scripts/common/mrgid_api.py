"""MarineRegions REST helpers with on-disk JSON caching.

Three resources are cached under `work/mrgid/api/`:

  types.json                              — `getGazetteerTypes`, list[dict]
  by_type/<safe-type-name>/<offset>.json  — one paginated page of records
  wms/<shard>/<MRGID>.json                — per-MRGID WMS pointer list

Pages are 100 records (server-enforced); an empty page terminates pagination.

The WMS pointer list returned by `getGazetteerWMSes` tells us, for one MRGID:
which GeoServer layer holds its polygon and which attribute column to filter
on. Pointers vary even within a single placeType (e.g. some IHO sea areas key
on `mrgid`, others on `id`), so we resolve them per record rather than
hard-coding a placeType→layer table.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

REST_BASE = "https://marineregions.org/rest"
WFS_BASE = "https://geo.vliz.be/geoserver"
PAGE_SIZE = 100
HTTP_TIMEOUT = 60
RETRIES = 4              # total attempts on transient failures
RETRY_BACKOFF = 2.0      # seconds, doubled each retry


def _get_with_retry(url: str, *, timeout: int = HTTP_TIMEOUT):
    """GET with exponential backoff on connection / read timeouts and 5xx.

    Returns the final Response (which may still be a 404 — that's not a
    "transient" failure and the caller handles it). Raises after RETRIES
    consecutive transient failures.
    """
    delay = RETRY_BACKOFF
    last_exc: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code in (500, 502, 503, 504):
                last_exc = requests.HTTPError(f"{r.status_code} on {url}")
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
    """Parallel-fetch WMS pointers. Tolerates per-MRGID failures (returns []).

    A failed lookup leaves no cache file, so re-running picks it up next time.
    """
    out: dict[int, list[WMSPointer]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_wms_pointers, m, cache_dir): m for m in mrgids}
        done = 0
        for fut in as_completed(futures):
            mrgid = futures[fut]
            try:
                out[mrgid] = fut.result()
            except Exception:
                out[mrgid] = []
            done += 1
            if on_progress and (done % 500 == 0 or done == len(mrgids)):
                on_progress(done, len(mrgids))
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
