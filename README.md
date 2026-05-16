# col-gazetteers

[![Validate ids against backend vocab](https://github.com/CatalogueOfLife/col-gazetteers/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/CatalogueOfLife/col-gazetteers/actions/workflows/test.yml)

▶ **Live viewer:** <https://catalogueoflife.github.io/col-gazetteers/> — interactive map with build metadata per gazetteer; pick any region to overlay it on OSM tiles.

GeoJSON gazetteer assets used by the [ChecklistBank backend](https://github.com/CatalogueOfLife/backend) to (a) resolve English labels for area ids in taxon distributions and (b) serve area geometries via the `/vocab/area/{prefix}:{id}` endpoint with `Accept: application/geo+json`.

This repo holds:

1. **Built assets** — a normalized on-disk tree (see [On-disk layout](#on-disk-layout)) consumed directly by the backend.
2. **Build scripts** — code that turns upstream sources (FAO, IHO, VLIZ/MarineRegions) into that tree.

The backend points at this tree via the `gazetteerDir` config key in `WsServerConfig`. The deploy repo unpacks/clones this repo onto each backend VM.

## Gazetteers in scope

The authoritative list of gazetteers (prefixes, titles, descriptions, upstream links) is the backend enum [`Gazetteer.java`](https://github.com/CatalogueOfLife/backend/blob/master/api/src/main/java/life/catalogue/api/vocab/area/Gazetteer.java). The prefixes below mirror that enum (excluding `text`, which has no geometry). Keep this table in sync with the enum as it evolves.

| Prefix | Name | Id format | Features | Upstream source | Build driver |
|---|---|---|---|---|---|
| `fao` | FAO Major Fishing Areas | 2-digit zone (e.g. `37`) | 19 | [VLIZ WFS `MarineRegions:fao`](https://geo.vliz.be/geoserver/MarineRegions/ows?service=WFS&version=2.0.0&request=GetFeature&typeNames=MarineRegions:fao&outputFormat=application/json) — top-level Major Fishing Areas only; hierarchical subareas like `37.4.1` would need a separate FAO Fisheries Division source. | [`scripts/fao/build.py`](scripts/fao/build.py) |
| `iho` | IHO Sea Areas (S-23, Limits of Oceans and Seas) | S-23 area number, case-sensitive (e.g. `23`, `28A`, `28a`) | 101 | [VLIZ WFS `MarineRegions:iho`](https://geo.vliz.be/geoserver/MarineRegions/ows?service=WFS&version=2.0.0&request=GetFeature&typeNames=MarineRegions:iho&outputFormat=application/json) | [`scripts/iho/build.py`](scripts/iho/build.py) |
| `mrgid` | MarineRegions Geographic IDs | integer MRGID (e.g. `8371`) | 808 | [VLIZ WFS](https://geo.vliz.be/geoserver/MarineRegions/ows) — curated union of 11 themed layers: `eez`, `lme`, `iho`, `fao`, `longhurst`, `high_seas`, `ecs`, `ices_areas`, `ices_ecoregions`, `arcticmarineareas`, `gazetteer_polygon`. | [`scripts/mrgid/build.py`](scripts/mrgid/build.py) |
| `tdwg` | TDWG WGSRPD | level-specific (e.g. `1`, `10`, `ABT`, `ABT-OO`) | 1039 | [tdwg/wgsrpd](https://github.com/tdwg/wgsrpd) — `geojson/level{1,2,3,4}.geojson` unified into one tree (9 + 52 + 369 + 609 features). | [`scripts/tdwg/build.py`](scripts/tdwg/build.py) |
| `iso` | ISO 3166-1 alpha-2 + ISO 3166-2 subdivisions, all upper-case | `US`, `DE`, `US-CA`, `DE-BY`, … | 4563 (250 countries + 4313 subdivisions) | [Natural Earth 10m cultural](https://naciscdn.org/naturalearth/10m/cultural/) — `ne_10m_admin_0_countries.zip` + `ne_10m_admin_0_map_subunits.zip` (fills NE's `ISO_A2 = -99` gaps via `ISO_A2_EH`) + `ne_10m_admin_1_states_provinces.zip`. Covers 250/251 of the backend's ISO 3166-1 enum; only `XZ` ("international waters") has no geometry. | [`scripts/iso/build.py`](scripts/iso/build.py) |
| `longhurst` | Longhurst Biogeographical Provinces | 4-letter `provcode` (e.g. `NADR`) | 54 | [VLIZ WFS `MarineRegions:longhurst`](https://geo.vliz.be/geoserver/MarineRegions/ows?service=WFS&version=2.0.0&request=GetFeature&typeNames=MarineRegions:longhurst&outputFormat=application/json) | [`scripts/longhurst/build.py`](scripts/longhurst/build.py) |
| `realm` | Biogeographic Realms — 8 traditional terrestrial realms | English name from `BioGeoRealm` (e.g. `Palearctic`, `Antarctic`) | 8 | [RESOLVE Ecoregions 2017](https://storage.googleapis.com/teow2016/Ecoregions2017.zip) (Dinerstein et al. 2017) dissolved by REALM. Spellings remapped: `Antarctica`→`Antarctic`, `Indomalayan`→`Indomalaya`. | [`scripts/realm/build.py`](scripts/realm/build.py) |
| `teow` | Terrestrial Ecoregions of the World | integer `ECO_ID` (e.g. `1`, `847`) | 847 | [RESOLVE Ecoregions 2017](https://storage.googleapis.com/teow2016/Ecoregions2017.zip) (Dinerstein et al. 2017, update of Olson 2001 WWF TEOW). | [`scripts/teow/build.py`](scripts/teow/build.py) |

### What's bundled in the backend, what lives here

This repo is the authoritative source of **labels** for `fao`, `iho`, `mrgid`, `tdwg`, `longhurst`, and `teow`, plus ISO 3166-2 subdivisions. The backend's `api` module still bundles compact label vocabularies only for `realm` (the 8-realm `BioGeoRealm` enum) and ISO 3166-1 country codes — everything else is read from this repo's `labels.tsv` files at startup.

For `iso`, this repo ships the full 3166 package — labels and geometries for both 3166-1 and 3166-2 — so it stays self-contained even though the backend has its own 3166-1 fallback.

Geometries are **never** part of the backend, so they always live here for every gazetteer above.

## On-disk layout

The backend expects exactly this structure under `gazetteerDir`:

```
<gazetteerDir>/
  fao/
    labels.tsv                      # one row per feature: <id>\t<english-name>
    features/<id>.geojson           # one GeoJSON Feature per file
    build.json                      # build provenance — see below
  iho/
    labels.tsv
    features/<id>.geojson
    build.json
  mrgid/
    labels.tsv
    features/<id>.geojson
    build.json
  iso/
    labels.tsv                      # full ISO 3166 — 3166-1 country + 3166-2 subdivision
    features/<id>.geojson           # ids include both `US` and `US-CA` style
    build.json
  tdwg/
    labels.tsv                      # LEVEL{1..4}_COD → LEVEL{1..4}_NAM (authoritative — backend no longer bundles)
    features/<id>.geojson
    build.json
  longhurst/
    labels.tsv                      # provcode → provdescr (authoritative — backend no longer bundles)
    features/<id>.geojson
    build.json
  realm/
    features/<id>.geojson           # labels bundled in backend; labels.tsv optional
    build.json
  teow/
    labels.tsv                      # ECO_ID → ECO_NAME (no backend bundle)
    features/<id>.geojson           # id = ECO_ID (integer 1..847)
    build.json
```

### `labels.tsv`

- UTF-8, no header, tab-delimited.
- Column 1: the area id as the backend will receive it (e.g. `37.4.1` for FAO, `8371` for MRGID — **without** the gazetteer prefix).
- Column 2: the English label.
- Order does not matter; the backend reads the file once at startup into a hash map.

### `features/<id>.geojson`

- A single GeoJSON `Feature` object (not a `FeatureCollection`).
- Filename is the bare id exactly as it appears in column 1 of `labels.tsv` (**case-preserved**), with whitespace / colons / slashes collapsed to `-`. The backend serves these as `<dir>/<prefix>/features/<id>.geojson` and rejects ids that don't normalize to a path under `features/`. Case-preservation matters: IHO S-23 distinguishes `28A` (Mediterranean Sea — Western Basin) from `28a` (Strait of Gibraltar) — the tree must therefore live on a **case-sensitive filesystem** (APFS-cs / ext4 / xfs). Default macOS HFS+ / APFS-ci will silently merge such filenames.
- `properties.name` should mirror the label in `labels.tsv` (the backend reads names from `labels.tsv`, not from the GeoJSON, but consumers fetching the GeoJSON expect a usable name).
- Geometries are in either **EPSG:4326** (WGS84 lon/lat — GeoJSON-native, RFC 7946 default) or **EPSG:3857** (Web Mercator — convenient for web-map overlays without client-side reprojection). The CRS is a build-time choice (see [`scripts/README.md`](scripts/README.md)) and is uniform across all gazetteers in a given build. Keep precision reasonable: 5–6 decimals for 4326, ~1 m (no decimals) for 3857.
- Geometries are simplified with Douglas–Peucker (see [Simplification](#simplification) below). Built tolerance is recorded in each `<prefix>/build.json` as `simplify_tolerance`.
- Multi-source prefixes (`mrgid`, `tdwg`, `iso`) stamp each feature with a `properties.source` string that names the upstream layer it came from — e.g. `mrgid:2401` (Baltic Sea per IHO) carries `"source": "iho-sea-area"`, while `mrgid:8538` (Baltic Sea per LME) carries `"source": "large-marine-ecosystem"`. Useful when one physical region exists as several authoritative variants.

### `build.json`

Per-gazetteer build manifest written by the script. Not consumed by the backend (which only reads `labels.tsv` and `features/`), but committed so reviewers and operators can see when the tree was last refreshed and from which exact upstream artifact. Format is JSON; example shape:

```json
{
  "prefix": "iho",
  "built_at": "2026-05-15T19:54:21Z",
  "crs": "EPSG:4326",
  "simplify_tolerance": 0.001,
  "feature_count": 99,
  "label_count": 99,
  "sources": [
    {
      "role": "shapefile",
      "name": "World_Seas_IHO_v3",
      "url": "https://www.marineregions.org/download_file.php?fn=World_Seas_IHO_v3",
      "filename": "World_Seas_IHO_v3.zip",
      "downloaded_at": "2026-05-15T19:50:11Z",
      "size_bytes": 12345678,
      "md5": "d41d8cd98f00b204e9800998ecf8427e",
      "sha256": "e3b0c44298fc1c149afbf4c8996fb924…",
      "upstream_version": "v3 (2021-11)"
    }
  ],
  "tools": {
    "ogr2ogr": "GDAL 3.8.4, released 2024/02/08",
    "python": "3.11.7",
    "build_script_commit": "4591267"
  }
}
```

Field notes:
- `sources` is an array because some prefixes pull from multiple inputs (e.g. `mrgid` = shapefile + REST API label enrichment). Each entry carries its own `url`, `md5`, etc.
- `md5` / `sha256` are computed over the downloaded artifact **as received** (the zip, not the extracted shapefiles).
- `upstream_version` is best-effort — empty string if the source doesn't expose a version.
- `build_script_commit` is the short SHA of `HEAD` at build time. Reviewers can pair it with `built_at` to reproduce the run.

### Contract

Anything outside the structure above is ignored by the backend. Sibling files (e.g. `fao/source.shp.zip`, `fao/build.log`) are fine but should not be committed — sources are gitignored under `/sources/` and re-fetched by the build scripts.

## Build scripts

See [`scripts/README.md`](scripts/README.md) for full detail. In short: Python 3.11+ orchestration, `ogr2ogr` (GDAL) for the heavy shapefile → GeoJSON conversion, `requests` for downloads. Each `scripts/<prefix>/build.py` is idempotent — pulls (or reuses cached) source, runs ogr2ogr, splits per feature, writes `<prefix>/labels.tsv` and `<prefix>/build.json`. Target CRS is configurable via `GAZETTEER_CRS=4326|3857`.

## Storage

Plain git. Features are text GeoJSON; git's pack format compresses them ~70 % (largest single feature is ~15 MB, well under GitHub's per-file limits). No Git LFS, no release tarballs — deploy just clones / pulls the repo.

## Simplification

All geometries are simplified with Douglas–Peucker via `ogr2ogr -simplify`. The tolerance is expressed in the units of the target CRS — **degrees** for EPSG:4326, **metres** for EPSG:3857. Douglas–Peucker guarantees no point in a built feature deviates from the original by more than the tolerance.

**Current default: `0.005°` (~550 m at the equator)** for EPSG:4326 builds, `500 m` for EPSG:3857.

The default was chosen empirically. We built `iho` (101 features, mostly complex sea boundaries) and `mrgid` (808 features, biggest tree) at three tolerances and compared:

| Tolerance | Real-world | `iho` total | `mrgid` total | Notes |
|---|---|---|---|---|
| `0.001°` | ~110 m | 74 MB | 196 MB | Sub-pixel detail at z6+; previous default. |
| **`0.005°`** | **~550 m** | **34 MB (−54 %)** | **94 MB (−52 %)** | **Current default.** Indistinguishable from `0.001°` at z2–z6 world view, big disk win. |
| `0.01°` | ~1.1 km | 28 MB (−62 %) | 76 MB (−61 %) | Visible degradation on small islands, narrow EEZs, fjord coastlines. |

So most of the win came at `0.005°`; tightening further to `0.01°` saves only ~20 % more disk for a noticeable loss of fidelity on small features. Since this tree is primarily consumed at world / regional zoom, `0.005°` is the sweet spot.

Override per build: `GAZETTEER_SIMPLIFY=0.0005 python scripts/iho/build.py`, or pass `--simplify 0.0005`. Same value is recorded into each `<prefix>/build.json`.

## Backend integration (reference)

In the backend repo (`CatalogueOfLife/backend`):

- [`life.catalogue.api.vocab.area.Gazetteer`](https://github.com/CatalogueOfLife/backend/blob/master/api/src/main/java/life/catalogue/api/vocab/area/Gazetteer.java) — the enum that defines every prefix this repo populates. Adding or removing a gazetteer requires changes in both repos.
- `WsServerConfig.gazetteerDir` — points at a checkout of this repo on each VM. Nullable; when unset, label lookups for `fao`/`iho`/`mrgid`/`tdwg`/`longhurst`/`teow` and ISO 3166-2 fall back to the raw id, labels for the still-bundled vocabularies (`realm`/ISO 3166-1) keep resolving from the backend's built-in tables, and the GeoJSON endpoint returns 404 for all prefixes.
- `life.catalogue.parser.AreaLabelLookup` — loads `labels.tsv` per gazetteer at startup into an in-memory map.
- `VocabResource#areaGeojson` — streams `<dir>/<prefix>/features/<id>.geojson` on `GET /vocab/area/{prefix}:{id}` with `Accept: application/geo+json`.

See the backend's `CLAUDE.md` and `XRELEASE.md` for the broader pipeline.

## Deploy

The `CatalogueOfLife/deploy` repo (private) needs to:
1. Clone this repo to a known path on each backend VM.
2. Run `git pull` on a cadence (or after release-cuts) to refresh data.
3. Point `gazetteerDir` in the env-specific Dropwizard config at the checkout.

## License

Scripts and metadata files in this repo: **Apache 2.0**.

Generated data files are derived from third-party sources, each with its own licence and citation requirement — see [`ATTRIBUTIONS.md`](ATTRIBUTIONS.md).
