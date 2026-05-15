# col-gazetteers

GeoJSON gazetteer assets used by the [ChecklistBank backend](https://github.com/CatalogueOfLife/backend) to (a) resolve English labels for area ids in taxon distributions and (b) serve area geometries via the `/vocab/area/{prefix}:{id}` endpoint with `Accept: application/geo+json`.

This repo holds:

1. **Built assets** — a normalized on-disk tree (see [On-disk layout](#on-disk-layout)) consumed directly by the backend.
2. **Build scripts** — code that turns upstream sources (FAO, IHO, VLIZ/MarineRegions) into that tree.

The backend points at this tree via the `gazetteerDir` config key in `WsServerConfig`. The deploy repo unpacks/clones this repo onto each backend VM.

## Gazetteers in scope

The authoritative list of gazetteers (prefixes, titles, descriptions, upstream links) is the backend enum [`Gazetteer.java`](https://github.com/CatalogueOfLife/backend/blob/master/api/src/main/java/life/catalogue/api/vocab/area/Gazetteer.java). The prefixes below mirror that enum (excluding `text`, which has no geometry), plus one extension (`teow`) that is not yet in the enum. Keep this table in sync with the enum as it evolves.

| Prefix | Name | Id format | Features | Upstream source | Build driver |
|---|---|---|---|---|---|
| `fao` | FAO Major Fishing Areas | 2-digit zone (e.g. `37`) | 19 | [VLIZ WFS `MarineRegions:fao`](https://geo.vliz.be/geoserver/MarineRegions/ows?service=WFS&version=2.0.0&request=GetFeature&typeNames=MarineRegions:fao&outputFormat=application/json) — top-level Major Fishing Areas only; hierarchical subareas like `37.4.1` would need a separate FAO Fisheries Division source. | [`scripts/fao/build.py`](scripts/fao/build.py) |
| `iho` | IHO Sea Areas (S-23, Limits of Oceans and Seas) | S-23 area number, case-sensitive (e.g. `23`, `28A`, `28a`) | 101 | [VLIZ WFS `MarineRegions:iho`](https://geo.vliz.be/geoserver/MarineRegions/ows?service=WFS&version=2.0.0&request=GetFeature&typeNames=MarineRegions:iho&outputFormat=application/json) | [`scripts/iho/build.py`](scripts/iho/build.py) |
| `mrgid` | MarineRegions Geographic IDs | integer MRGID (e.g. `8371`) | 808 | [VLIZ WFS](https://geo.vliz.be/geoserver/MarineRegions/ows) — curated union of 11 themed layers: `eez`, `lme`, `iho`, `fao`, `longhurst`, `high_seas`, `ecs`, `ices_areas`, `ices_ecoregions`, `arcticmarineareas`, `gazetteer_polygon`. | [`scripts/mrgid/build.py`](scripts/mrgid/build.py) |
| `tdwg` | TDWG WGSRPD | level-specific (e.g. `1`, `10`, `ABT`, `ABT-OO`) | 1039 | [tdwg/wgsrpd](https://github.com/tdwg/wgsrpd) — `geojson/level{1,2,3,4}.geojson` unified into one tree (9 + 52 + 369 + 609 features). | [`scripts/tdwg/build.py`](scripts/tdwg/build.py) |
| `iso` | ISO 3166-1 alpha-2 + ISO 3166-2 subdivisions, all upper-case | `US`, `DE`, `US-CA`, `DE-BY`, … | 4548 (235 countries + 4313 subdivisions) | [Natural Earth 10m cultural](https://naciscdn.org/naturalearth/10m/cultural/) — `ne_10m_admin_0_countries.zip` + `ne_10m_admin_1_states_provinces.zip`. Features lacking a valid ISO code are dropped. | [`scripts/iso/build.py`](scripts/iso/build.py) |
| `longhurst` | Longhurst Biogeographical Provinces | 4-letter `provcode` (e.g. `NADR`) | 54 | [VLIZ WFS `MarineRegions:longhurst`](https://geo.vliz.be/geoserver/MarineRegions/ows?service=WFS&version=2.0.0&request=GetFeature&typeNames=MarineRegions:longhurst&outputFormat=application/json) | [`scripts/longhurst/build.py`](scripts/longhurst/build.py) |
| `realm` | Biogeographic Realms — 8 traditional terrestrial realms | English name from `BioGeoRealm` (e.g. `Palearctic`, `Antarctic`) | 8 | [RESOLVE Ecoregions 2017](https://storage.googleapis.com/teow2016/Ecoregions2017.zip) (Dinerstein et al. 2017) dissolved by REALM. Spellings remapped: `Antarctica`→`Antarctic`, `Indomalayan`→`Indomalaya`. | [`scripts/realm/build.py`](scripts/realm/build.py) |
| `teow` | Terrestrial Ecoregions of the World (extension, **not yet in enum**) | integer `ECO_ID` (e.g. `1`, `847`) | 847 | [RESOLVE Ecoregions 2017](https://storage.googleapis.com/teow2016/Ecoregions2017.zip) (Dinerstein et al. 2017, update of Olson 2001 WWF TEOW). Built and committed; **not yet deployed** — `Gazetteer.java` needs a `TEOW` entry before the backend can serve these. | [`scripts/teow/build.py`](scripts/teow/build.py) |

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

Anything outside the structure above is ignored by the backend. Sibling files (e.g. `fao/source.shp.zip`, `fao/build.log`) are fine but should not be committed — sources are gitignored under `/sources/` and re-fetched by the build scripts.

## Build scripts

See [`scripts/README.md`](scripts/README.md) for full detail. In short: Python 3.11+ orchestration, `ogr2ogr` (GDAL) for the heavy shapefile → GeoJSON conversion, `requests` for downloads. Each `scripts/<prefix>/build.py` is idempotent — pulls (or reuses cached) source, runs ogr2ogr, splits per feature, writes `<prefix>/labels.tsv` and `<prefix>/build.json`. Target CRS is configurable via `GAZETTEER_CRS=4326|3857`.

## Storage

Plain git. Features are text GeoJSON; git's pack format compresses them ~70 % (largest single feature is ~15 MB, well under GitHub's per-file limits). No Git LFS, no release tarballs — deploy just clones / pulls the repo.

## Open questions

- Do we ship simplified + full geometries (e.g. `features/<id>.geojson` simplified, `features/<id>.full.geojson` original)? Backend currently expects one file.
- MRGID coverage: extend the layer union past the current 11 themed layers, or stick with the curated baseline?
- Licensing / attribution: each source has its own license (FAO terms, VLIZ CC-BY 4.0 for MRGID, Natural Earth public domain, RESOLVE Ecoregions CC-BY 4.0, IHO usage policy). Add an `ATTRIBUTIONS.md` capturing per-source terms; the scripts themselves are Apache-2.0.
- CI: nightly job to rebuild and push? Or manual on source updates?
- Add `TEOW` to `Gazetteer.java` so the backend can serve the `teow` features already shipped here.

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
