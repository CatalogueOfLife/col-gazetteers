# Build scripts

Python build scripts that turn upstream sources into the on-disk tree consumed by the backend (see top-level [`README.md`](../README.md)).

## Approach

- **Python 3.11+** orchestration. Each gazetteer has a `build.py` driver.
- **GDAL/`ogr2ogr`** does the heavy lifting: shapefile reading, reprojection, Douglas–Peucker simplification, and per-feature splitting. Called via `subprocess` from Python.
- **`requests`** for source downloads (including the MarineRegions REST API for MRGID label enrichment).
- Standard-library `json`, `csv`, `pathlib` — no `geopandas`/`fiona` needed. Keeps the dependency surface small; `ogr2ogr` is already a hard requirement for shapefile handling.

## Output CRS — configurable

Target CRS is set repo-wide via the `GAZETTEER_CRS` env var (also a `--crs` CLI flag on each `build.py`), read by `scripts/common/config.py`. Supported values:

| Value | EPSG | Units | When to pick it |
|---|---|---|---|
| `4326` (default) | EPSG:4326 WGS84 lon/lat | degrees | GeoJSON-native ([RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946#section-4)); most consumers expect this. |
| `3857` | EPSG:3857 Web Mercator | metres | Direct overlay on web map tiles (Leaflet/OpenLayers/Maplibre) with no client reprojection. Technically deviates from RFC 7946, which is acceptable for our backend's `application/geo+json` payloads — flag this to API consumers. |

The full build (download, ogr2ogr, split, simplify) runs in the chosen CRS — there is **no** mixed-CRS output. To switch, re-run the build with the new value; the on-disk tree is replaced wholesale.

`ogr2ogr` flags wired by `common/ogr.py`:

| | EPSG:4326 | EPSG:3857 |
|---|---|---|
| `-t_srs` | `EPSG:4326` | `EPSG:3857` |
| `-simplify` tolerance default | `0.005` (≈ 550 m at equator) | `500` (metres) |
| Coordinate rounding | 6 decimals | 0 decimals |

Per-gazetteer overrides for tolerance live next to each `build.py` — `gi` sets `0.002°` (islands are small and intricate enough that the `0.005°` default flattens 6.4 % of them to a triangle) and is the one build that wires up the `--simplify` flag. The effective value is always recorded in `<prefix>/build.json`.

## Layout

```
scripts/
  common/
    config.py          # reads GAZETTEER_CRS (4326|3857), exposes target SRS + defaults
    download.py        # cached HTTP download → sources/<prefix>/...; returns SourceRecord (url, filename, md5, sha256, size, downloaded_at)
    ogr.py             # ogr2ogr wrappers: shp_to_geojson, split_by_field, simplify (CRS-aware); returns ogr2ogr version
    labels.py          # write labels.tsv from a list of (id, name) rows
    ids.py             # filename normalization + collision checks
    manifest.py        # accumulate provenance during a build; write build.json at the end
  fao/      build.py
  iho/      build.py
  mrgid/    build.py
  iso/      build.py
  tdwg/     build.py
  longhurst/ build.py
  realm/    build.py
  teow/     build.py
  wdpa/     build.py         # labels-only (no GDAL): WDPA CSV → labels.tsv
  build_all.py         # invokes each build.py in turn
  pyproject.toml       # deps: requests, click (CLI); ruff for lint
```

Each `build.py`:
1. Resolves source (download to `/sources/<prefix>/` if not already cached). `common/download.py` returns a `SourceRecord` carrying url, filename, size, md5, sha256, downloaded-at — fed to the manifest builder.
2. Converts to GeoJSON (one Feature per `<id>.geojson`).
3. Writes `<prefix>/labels.tsv` (skipped for prefixes whose labels are bundled in the backend — see top-level README).
4. Writes `<prefix>/build.json` via `common/manifest.py` (see schema in top-level README). The manifest helper records: `built_at` (UTC), `crs`, `simplify_tolerance`, `feature_count`, `label_count`, every `SourceRecord` from step 1, and tool versions (`ogr2ogr --version`, `sys.version`, current git `HEAD` short SHA).
5. Is idempotent (`--force` re-downloads; default reuses cached source). Even when source is reused from cache, the manifest is regenerated each run so `built_at` reflects the latest conversion.

## Source map (VLIZ-primary)

| Prefix | Primary source | Notes |
|---|---|---|
| `fao` | [VLIZ MarineRegions — FAO Major Fishing Areas](https://www.marineregions.org/sources.php#fao) shapefile | Id = `F_CODE` (e.g. `37.4.1`). Name = `NAME_EN`. ~30 features incl. subareas. |
| `iho` | [VLIZ MarineRegions — IHO Sea Areas v3](https://www.marineregions.org/sources.php#iho) shapefile | Id = `MRGID` (or `NAME` slug — decide). Name = `NAME`. ~100 features. |
| `mrgid` | [VLIZ MarineRegions — full gazetteer download](https://www.marineregions.org/downloads.php) shapefile + [REST API](https://www.marineregions.org/gazetteer.php?p=webservices) for `preferredGazetteerName` | Id = `MRGID`. Hundreds of thousands of features → drives storage strategy (LFS / release tarball). Consider curated subset (EEZ + IHO + Longhurst + only MRGIDs referenced by CoL data). |
| `longhurst` | [VLIZ MarineRegions — Longhurst Provinces v4](https://www.marineregions.org/sources.php#longhurst) shapefile | Id = `ProvCode` (4-letter). Name = `ProvDescr`. ~50 features. |
| `iso` | **Not VLIZ.** [Natural Earth admin_0 + admin_1](https://www.naturalearthdata.com/downloads/) | Id = `ISO_A2` for 3166-1, `iso_3166_2` for subdivisions. Backend lacks 3166-2 labels, so labels.tsv is authoritative here. |
| `tdwg` | **Not VLIZ.** [tdwg/wgsrpd GeoJSON](https://github.com/tdwg/wgsrpd) | Levels 1–4. Id = `LEVELn_CODE`. Labels bundled in backend; labels.tsv optional. |
| `realm` | **Not VLIZ.** TBD — likely [WWF Terrestrial Ecoregions](https://www.worldwildlife.org/publications/terrestrial-ecoregions-of-the-world) (Olson et al. 2001) | Id = realm code (`NA`, `PA`, `AT`, …). Labels bundled in backend; labels.tsv optional. |
| `gi` | **Not VLIZ.** [Global Islands file geodatabase](https://www.arcgis.com/home/item.html?id=885a860af66d4833887dcce735a521a7) (USGS / Esri / UNEP-WCMC, v3), an 818 MB `GlbIslands.gdb.zip` served straight from ArcGIS Online. | Id = `ALL_Uniq` (unique across all size classes; `USGS_ISID` is not). Name = `Name_USGSO` only — the WCMC name columns arrived via a nearest-neighbour join and mislabel islets with their neighbour's name. `BigIslands` layer only (>1 km²), named subset: 15,139 features. Unzipped into `sources/gi/` rather than read via `/vsizip/`. Simplify `0.002°`. |
| `wdpa` | **Not VLIZ.** [Protected Planet](https://www.protectedplanet.net/) monthly global public **CSV** release (UNEP-WCMC & IUCN). | **Labels only — no geometry** (WDPA license forbids redistribution). Id = `SITE_ID` (WDPAID). Name = `NAME_ENG`. Reads the attribute-only CSV (no GDAL), deduped per `SITE_ID`. `--month Jun2026` overrides the release token. |

VLIZ covers 4 of 8 geometry prefixes directly. `iso`, `tdwg`, `realm`, `teow` follow the same pipeline from non-VLIZ sources; `wdpa` is labels-only and uses no GDAL.

## Common pipeline (per gazetteer)

```
sources/<prefix>/<archive>.zip       # downloaded, gitignored
        │
        ▼  unzip + ogr2ogr (reproject to target CRS, simplify with CRS-appropriate tolerance)
work/<prefix>/all.geojson            # gitignored intermediate
        │
        ▼  split: one Feature → features/<id>.geojson; collect (id, name) rows
<prefix>/features/<id>.geojson       # committed (or LFS/release for mrgid)
<prefix>/labels.tsv                  # committed
```

Filename normalization: lowercase, replace `/` and `:` with `-`, no whitespace. The `ids` helper enforces this and errors on collisions.

Geometry normalization: `ogr2ogr -makevalid` can turn a self-intersecting polygon into a `GeometryCollection` of polygons plus stray lines/points. `split_features` collapses that to its polygonal parts on the way out, and drops (with a log line) any feature left with nothing polygonal — the backend contract is one polygonal `Feature` per file.


## SQL dialects

Builds that filter or aggregate must use **OGRSQL**, not the SQLite dialect: `-dialect SQLITE` is not available in every GDAL build (it fails with `OGR2SQLITE_Setup() failed due to sqlite3_api == nullptr`, e.g. on Homebrew GDAL 3.8.5). OGRSQL has no `TRIM()`, which matters for sources that pad blank strings — Global Islands returns an absent name as a single space, *and* uses the literal placeholder `UNNAMED` for 6,659 more, so `gi` tests all three values explicitly. Worth checking any new source for both failure modes: an is-not-empty test caught neither.


## Running

```
cd scripts
python -m venv .venv && source .venv/bin/activate
pip install -e .                     # or: pip install -r requirements.txt
python build_all.py                          # rebuild everything in EPSG:4326 (default)
GAZETTEER_CRS=3857 python build_all.py       # rebuild everything in EPSG:3857
python fao/build.py --force --crs 3857       # rebuild one, CLI overrides env
```

Requires `ogr2ogr` on PATH (`brew install gdal` / `apt install gdal-bin`).
