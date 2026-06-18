# WDPA gazetteer — design

**Date:** 2026-06-18
**Status:** Approved (pending spec review)
**Prefix:** `wdpa`

## Goal

Add the **World Database on Protected Areas** (WDPA, incl. WDOECM) from
[protectedplanet.net](https://www.protectedplanet.net/) as a new gazetteer in
`col-gazetteers`, so the ChecklistBank backend can (a) resolve English labels
for `wdpa:{id}` area references in taxon distributions and (b) serve protected-area
geometries via `/vocab/area/wdpa:{id}` with `Accept: application/geo+json`.

This is **net-new capability**: WDPA is not yet in the backend's authoritative
[`Gazetteer.java`](https://github.com/CatalogueOfLife/backend/blob/master/api/src/main/java/life/catalogue/api/vocab/area/Gazetteer.java)
enum, so the work spans two repos (backend enum + this repo's build/assets).

## Decisions

| Question | Decision |
|---|---|
| Scope | **Full dataset** — all ~300k protected areas (WDPA + WDOECM combined public download). |
| Feature id | **One feature per integer `WDPAID`**; multi-parcel PAs (multiple `WDPA_PID` rows) dissolved/unioned into one geometry. Id regex `^[0-9]+$`. |
| Points layer | **Include** point-only PAs (`WDPA_point`) as `Point` geometries. Polygon wins when a WDPAID exists in both layers. |
| Source fetch | **Monthly global File Geodatabase** (`WDPA_<Mon><Year>_Public.gdb.zip`) from Protected Planet's CloudFront URL, via the existing cached `common/download.py`. |
| Storage | **Plain commit** of the feature tree, consistent with `mrgid` (no LFS, no release tarball). |
| Licensing | Proceed with full geometries; WDPA Terms & Conditions to be handled by the maintainer. Required WDPA citation recorded in `ATTRIBUTIONS.md` and `build.json`. |
| Dissolve mechanism | **`ogr2ogr` SQLite dialect** (`ST_Union` + `GROUP BY WDPAID`), staying within the repo's "ogr2ogr + stdlib, no geopandas" rule. |
| Output CRS | Repo-wide `GAZETTEER_CRS` (default `4326`). WDPA source is already EPSG:4326. |

## Build pipeline (`scripts/wdpa/build.py`)

Follows the standard per-gazetteer driver contract (download → convert → split →
labels → manifest; idempotent, `--force` re-downloads, `--crs` override).

```
download WDPA_<Mon><Year>_Public.gdb.zip  → sources/wdpa/   (cached, gitignored)
  │  ogrinfo to discover the date-stamped layer names (WDPA_poly_*, WDPA_point_*)
  ▼
work/wdpa/poly.geojson   ← ogr2ogr -dialect SQLITE:
  │                          SELECT WDPAID, MIN(NAME) AS NAME, ST_Union(geometry) AS geometry
  │                          FROM <WDPA_poly layer> GROUP BY WDPAID
  │                          + reproject to target CRS + simplify
work/wdpa/point.geojson  ← ogr2ogr point layer, only WDPAIDs absent from poly.geojson
  │  merge the two (polygon precedence)
  ▼
wdpa/features/<WDPAID>.geojson   (committed; one GeoJSON Feature per file)
wdpa/labels.tsv                  WDPAID → NAME   (committed; UTF-8, tab-delimited, no header)
wdpa/build.json                  provenance via common/manifest.py
```

### Details

- **Layer discovery:** GDB layer names are date-stamped (e.g. `WDPA_poly_<Mon><Year>`).
  Discover them with `ogrinfo` rather than hard-coding the month.
- **Name field:** use `NAME` (English name) for both `labels.tsv` and
  `properties.name`. (`ORIG_NAME` is the original-language name; not used.)
- **Dissolve:** `ST_Union` groups all parcel rows of a WDPAID into one geometry,
  in source CRS, before reproject + simplify.
- **Points merge:** a PA is normally either polygon or point. For any WDPAID present
  only in the point layer, emit a `Point` feature; polygon geometry always wins on overlap.
- **Simplification tolerance:** a per-gazetteer override **more aggressive than the
  0.005 (4326) default** — start at ~0.01 — since WDPA polygons are highly detailed
  and tolerance is the main lever on the on-disk footprint. Tune after a trial build.
- **`properties.source`:** stamp which upstream layer each feature came from
  (`WDPA_poly` / `WDPA_point`), matching the per-feature provenance other large
  builds (mrgid/iso/tdwg) carry.
- **build.json:** records the standard manifest fields (`built_at`, `crs`,
  `simplify_tolerance`, `feature_count`, `label_count`, every `SourceRecord`,
  tool versions, git HEAD).

## Other files / repos touched

- **backend `Gazetteer.java`** — add a `WDPA` enum entry:
  - title: "World Database on Protected Areas"
  - link: `https://www.protectedplanet.net/`
  - areaLink template: `https://www.protectedplanet.net/` (→ `.../<WDPAID>`)
  - description: WDPA/WDOECM one-liner (UNEP-WCMC & IUCN)
  - caseSensitive: `false`; pattern: `^[0-9]+$`; normalizer: `null`; areaClass: `GenericArea.class`
  - **Must be deployed before `test_id_patterns.py` passes**, since that test reads
    the live `/vocab/gazetteer` regex from the API.
- **`scripts/test_id_patterns.py`** — add `wdpa` to `EXTENSION_PREFIXES` until the
  backend enum entry is live in the queried API; remove once it is.
- **viewer `index.html`** — add `wdpa` to `PREFIX_ORDER`, the `NAMES` map, and a
  hand-picked sample id list (analogous to mrgid's 5 oceans). The viewer must **not**
  attempt to load all ~300k features.
- **`README.md`** + **`scripts/README.md`** — add the `wdpa` row to the gazetteer
  tables (prefix, name, id format, feature count, upstream source, build driver).
- **`ATTRIBUTIONS.md`** — WDPA citation: UNEP-WCMC and IUCN, `<Year>`, Protected
  Planet: The World Database on Protected Areas (WDPA), `<Month/Year>`, Cambridge,
  UK: UNEP-WCMC and IUCN. Available at: www.protectedplanet.net.

## Risks

- **Repo size:** even aggressively simplified, ~300k detailed polygons could push the
  repo to several GB. Measure on a trial build and report the footprint **before**
  committing the full feature tree; retune the simplify tolerance if needed.
- **Licensing:** the geometries are redistributed publicly via this repo and the CLB
  API — covered by the maintainer's handling of WDPA Terms & Conditions. Citation is
  recorded but does not by itself grant redistribution rights.
- **Backend coupling:** the id-pattern test will fail until the `WDPA` enum entry is
  deployed to the API the test queries. Sequencing: ship enum → deploy → drop the
  `EXTENSION_PREFIXES` exemption.

## Out of scope

- Curated/subset builds (full dataset chosen).
- Per-parcel (`WDPA_PID`) granularity.
- WDPA REST API ingestion (bulk GDB download chosen).
- LFS / release-tarball storage (plain commit chosen).
