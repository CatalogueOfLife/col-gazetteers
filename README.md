# col-gazetteers

GeoJSON gazetteer assets used by the [ChecklistBank backend](https://github.com/CatalogueOfLife/backend) to (a) resolve English labels for area ids in taxon distributions and (b) serve area geometries via the `/vocab/area/{prefix}:{id}` endpoint with `Accept: application/geo+json`.

This repo holds:

1. **Built assets** — a normalized on-disk tree (see [On-disk layout](#on-disk-layout)) consumed directly by the backend.
2. **Build scripts** — code that turns upstream sources (FAO, IHO, VLIZ/MarineRegions) into that tree.

The backend points at this tree via the `gazetteerDir` config key in `WsServerConfig`. The deploy repo unpacks/clones this repo onto each backend VM.

## Gazetteers in scope

The authoritative list of gazetteers (prefixes, titles, descriptions, upstream links) is the backend enum [`Gazetteer.java`](https://github.com/CatalogueOfLife/backend/blob/master/api/src/main/java/life/catalogue/api/vocab/area/Gazetteer.java). The prefixes below mirror that enum (excluding `text`, which has no geometry), plus one extension (`teow`) that is not yet in the enum. Keep this table in sync with the enum as it evolves.

| Prefix | Name | Source |
|---|---|---|
| `fao` | FAO Major Fishing Areas | [VLIZ / MarineRegions FAO](https://geo.vliz.be/geoserver/MarineRegions/ows) — WFS layer `MarineRegions:fao` (top-level Major Fishing Areas only; subareas like `37.4.1` need a separate FAO Fisheries Division source). |
| `iho` | IHO Sea Areas (Limits of Oceans and Seas, S-23) | [VLIZ / MarineRegions IHO](https://geo.vliz.be/geoserver/MarineRegions/ows) — WFS layer `MarineRegions:iho`. Keyed by S-23 area number. |
| `mrgid` | MarineRegions Geographic IDs | [VLIZ / MarineRegions](https://geo.vliz.be/geoserver/MarineRegions/ows) — curated union of 11 themed WFS layers (eez, lme, iho, fao, longhurst, high_seas, ecs, ices_areas, ices_ecoregions, arcticmarineareas, gazetteer_polygon). |
| `tdwg` | TDWG World Geographical Scheme for Recording Plant Distributions (WGSRPD) | [tdwg/wgsrpd](https://github.com/tdwg/wgsrpd) — GeoJSON levels 1–4 unified into one tree. |
| `iso` | ISO 3166 country and subdivision codes (3166-1 + 3166-2) | TBD — geometries likely from [Natural Earth](https://www.naturalearthdata.com/) or GADM |
| `longhurst` | Longhurst Biogeographical Provinces | [VLIZ / MarineRegions Longhurst](https://geo.vliz.be/geoserver/MarineRegions/ows) — WFS layer `MarineRegions:longhurst`. Keyed by 4-letter `provcode`. |
| `realm` | Biogeographic Realms (8 traditional terrestrial realms) | [Biogeographic realm — Wikipedia](https://en.wikipedia.org/wiki/Biogeographic_realm) (definition); geometry from [RESOLVE Ecoregions 2017](https://storage.googleapis.com/teow2016/Ecoregions2017.zip) dissolved by REALM into the 8 realms named by `BioGeoRealm`. |
| `teow` | Terrestrial Ecoregions of the World — ~847 ecoregions keyed by `ECO_ID` | [RESOLVE Ecoregions 2017](https://storage.googleapis.com/teow2016/Ecoregions2017.zip) (Dinerstein et al. 2017, update of Olson 2001 WWF TEOW). **Not yet in `Gazetteer.java`** — backend enum needs a `TEOW` entry before CoL data can reference these. |

### What's bundled in the backend, what lives here

For `tdwg`, `longhurst`, and `realm`, the backend's `api` module already bundles labels as compact vocabularies — this repo only needs to provide **geometries** for them. The `labels.tsv` is optional for these prefixes.

For `iso`, the backend bundles only ISO 3166-1 country labels; it does **not** know ISO 3166-2 subdivisions. This repo therefore provides the full ISO 3166 package — labels and geometries for both 3166-1 and 3166-2 — and is the authoritative source for the 3166-2 part.

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
    features/<id>.geojson           # labels bundled in backend; labels.tsv optional
    build.json
  longhurst/
    features/<id>.geojson           # labels bundled in backend; labels.tsv optional
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
- Prefer simplified geometries (Douglas–Peucker, ~10–100 m tolerance) when source shapefiles are highly detailed; raw VLIZ MRGID shapes can be hundreds of MB per feature.

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

Anything outside the structure above is ignored by the backend. Adding sibling files (e.g. `fao/source.shp.zip`, `fao/build.log`) is fine but **should not** be committed unless small — see [Storage strategy](#storage-strategy).

## Build scripts

Layout (TBD — define in the first implementation pass):

```
scripts/
  fao/         # download FAO shapefile + names → labels.tsv + features/
  iho/         # download IHO sea-areas shapefile from VLIZ → labels.tsv + features/
  mrgid/       # download full VLIZ MRGID gazetteer → labels.tsv + features/
  iso/         # ISO 3166-1 + 3166-2 labels and geometries
  tdwg/        # TDWG WGSRPD GeoJSON → features/ (no labels.tsv needed)
  longhurst/   # Longhurst provinces shapefile → features/ (no labels.tsv needed)
  realm/       # biogeographic realms → features/ (no labels.tsv needed)
  common/      # shared helpers (shapefile → GeoJSON, geometry simplification)
```

Language: open. **Python with `geopandas` / `fiona` / `shapely`** is the default recommendation — fits the data wrangling, easy CI, good shapefile support. Each `scripts/<prefix>/build.sh` (or `build.py`) should be idempotent: pull source → write into `../<prefix>/labels.tsv` and `../<prefix>/features/`.

## Storage strategy

MRGID alone is ~hundreds of thousands of features. We do **not** want to commit hundreds of MB of GeoJSON into git history. Options to decide in the first session:

1. **Git LFS** for `*/features/*.geojson` (and source archives if kept).
2. **Release artifacts** — build script produces a tarball published as a GitHub release; deploy fetches the tarball; git history holds only scripts + small `labels.tsv` files.
3. **Per-gazetteer trade-off** — FAO and IHO are small (<1 MB total) and fine to commit; MRGID via LFS or releases.

`labels.tsv` is small (a few MB even for full MRGID) and should always live in git.

Track this decision in the repo before pushing real data.

## Open questions for the first implementation session

- Which Python deps / Node toolchain do we standardize on for scripts?
- Storage strategy (see above) — decide and document.
- Do we ship simplified + full geometries (e.g. `features/<id>.geojson` simplified, `features/<id>.full.geojson` original)? Backend currently expects one file.
- MRGID coverage: full VLIZ export, or curated subset (EEZs, IHO seas, Longhurst — only what's referenced from CoL data)? Full was the agreed direction but the size is what motivates the storage decision.
- Licensing / attribution: each source has its own license (FAO terms, VLIZ CC-BY 4.0 for MRGID, IHO usage policy). Add a `LICENSES/` or `ATTRIBUTIONS.md` capturing per-source terms; the scripts themselves are Apache-2.0.
- CI: nightly job to rebuild and push? Or manual on source updates?

## Backend integration (reference)

In the backend repo (`CatalogueOfLife/backend`):

- [`life.catalogue.api.vocab.area.Gazetteer`](https://github.com/CatalogueOfLife/backend/blob/master/api/src/main/java/life/catalogue/api/vocab/area/Gazetteer.java) — the enum that defines every prefix this repo populates. Adding or removing a gazetteer requires changes in both repos.
- `WsServerConfig.gazetteerDir` — points at a checkout of this repo on each VM. Nullable; when unset, label lookups for `fao`/`iho`/`mrgid` and ISO 3166-2 fall back to the raw id, labels for the bundled vocabularies (`tdwg`/`longhurst`/`realm`/ISO 3166-1) still resolve from the backend's built-in tables, and the GeoJSON endpoint returns 404 for all prefixes.
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

Generated data files are derived from third-party sources, each with its own license — see `ATTRIBUTIONS.md` (to be created in the first implementation session).
