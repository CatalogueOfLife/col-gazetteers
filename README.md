# col-gazetteers

GeoJSON gazetteer assets used by the [ChecklistBank backend](https://github.com/CatalogueOfLife/backend) to (a) resolve English labels for area ids in taxon distributions and (b) serve area geometries via the `/vocab/area/{prefix}:{id}` endpoint with `Accept: application/geo+json`.

This repo holds:

1. **Built assets** — a normalized on-disk tree (see [On-disk layout](#on-disk-layout)) consumed directly by the backend.
2. **Build scripts** — code that turns upstream sources (FAO, IHO, VLIZ/MarineRegions) into that tree.

The backend points at this tree via the `gazetteerDir` config key in `WsServerConfig`. The deploy repo unpacks/clones this repo onto each backend VM.

## Gazetteers in scope

| Prefix | Name | Source |
|---|---|---|
| `fao` | FAO Major Fishing Areas | [FAO Statistics Division](https://www.fao.org/fishery/en/area) — shapefile + names CSV |
| `iho` | IHO Sea Areas (Limits of Oceans and Seas, S-23) | [VLIZ / MarineRegions IHO](https://www.marineregions.org/sources.php#iho) |
| `mrgid` | MarineRegions Geographic IDs | [VLIZ / MarineRegions full gazetteer](https://www.marineregions.org/gazetteer.php) — shapefiles + REST API |

Other backend-recognized gazetteers (`tdwg`, `iso`, `longhurst`, `realm`) are bundled in the backend's `api` module as compact vocabularies and do **not** need to live here. They may later be extended with geometry exports — out of scope until requested.

## On-disk layout

The backend expects exactly this structure under `gazetteerDir`:

```
<gazetteerDir>/
  fao/
    labels.tsv                      # one row per feature: <id>\t<english-name>
    features/<id>.geojson           # one GeoJSON Feature per file
  iho/
    labels.tsv
    features/<id>.geojson
  mrgid/
    labels.tsv
    features/<id>.geojson
```

### `labels.tsv`

- UTF-8, no header, tab-delimited.
- Column 1: the area id as the backend will receive it (e.g. `37.4.1` for FAO, `8371` for MRGID — **without** the gazetteer prefix).
- Column 2: the English label.
- Order does not matter; the backend reads the file once at startup into a hash map.

### `features/<id>.geojson`

- A single GeoJSON `Feature` object (not a `FeatureCollection`).
- Filename is the bare id, lowercased exactly as it appears in column 1 of `labels.tsv`. **No colons, no slashes** — the backend serves these as `<dir>/<prefix>/features/<id>.geojson` and rejects ids that don't normalize to a path under `features/`.
- `properties.name` should mirror the label in `labels.tsv` (the backend reads names from `labels.tsv`, not from the GeoJSON, but consumers fetching the GeoJSON expect a usable name).
- Geometries should be in EPSG:4326 (WGS84 lon/lat). Keep precision reasonable (5–6 decimals).
- Prefer simplified geometries (Douglas–Peucker, ~10–100 m tolerance) when source shapefiles are highly detailed; raw VLIZ MRGID shapes can be hundreds of MB per feature.

### Contract

Anything outside the structure above is ignored by the backend. Adding sibling files (e.g. `fao/source.shp.zip`, `fao/build.log`) is fine but **should not** be committed unless small — see [Storage strategy](#storage-strategy).

## Build scripts

Layout (TBD — define in the first implementation pass):

```
scripts/
  fao/         # download FAO shapefile + names → labels.tsv + features/
  iho/         # download IHO sea-areas shapefile from VLIZ → labels.tsv + features/
  mrgid/       # download full VLIZ MRGID gazetteer → labels.tsv + features/
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

- `WsServerConfig.gazetteerDir` — points at a checkout of this repo on each VM. Nullable; when unset, FAO/IHO/MRGID label lookups fall back to the raw id and the GeoJSON endpoint returns 404.
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
