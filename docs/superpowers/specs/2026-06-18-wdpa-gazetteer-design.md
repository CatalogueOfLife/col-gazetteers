# WDPA gazetteer — design

**Date:** 2026-06-18
**Status:** Approved (pending spec review) — **labels-only** (see Licensing)
**Prefix:** `wdpa`

## Goal

Add the **World Database on Protected Areas** (WDPA, incl. WDOECM) from
[protectedplanet.net](https://www.protectedplanet.net/) as a new gazetteer in
`col-gazetteers`, so the ChecklistBank backend can resolve English **labels** for
`wdpa:{id}` area references in taxon distributions.

**Geometries are deliberately excluded.** WDPA's Terms & Conditions prohibit
redistribution of the data to third parties, which a public repo + the CLB
`/vocab/area` GeoJSON endpoint would violate. This gazetteer therefore ships
**only `labels.tsv`** (no `features/`). For `wdpa:{id}`, the backend resolves the
name and links out to `https://www.protectedplanet.net/<WDPAID>` via its
`areaLinkTemplate`; it does not serve geometry.

The backend `Gazetteer.WDPA` enum entry is **already deployed** (live at
`/vocab/gazetteer`: title "World Database on Protected Areas", pattern `^[0-9]+$`,
caseSensitive `false`, areaClass `GenericArea`, areaLinkTemplate
`https://www.protectedplanet.net/`). No further backend work is required.

## Decisions

| Question | Decision |
|---|---|
| Licensing | **Labels only — no geometry redistribution.** WDPA T&C prohibit redistributing the data; we ship `labels.tsv` only. Required WDPA citation recorded in `ATTRIBUTIONS.md` and `build.json`. |
| Scope | **All ~300k protected areas** that have a `WDPAID` + `NAME` in the public release (WDPA + WDOECM). |
| Id | **One label row per integer `WDPAID`**; deduplicated across multi-parcel rows and across the poly + point tables. Matches deployed pattern `^[0-9]+$`. |
| Source fetch | **Monthly attribute-only CSV** (`WDPA_<Mon><Year>_Public_csv.zip`) from Protected Planet's CloudFront URL, via the existing cached `common/download.py`. Tens of MB; contains WDPAID + NAME for both poly and point records, no geometry. Avoids downloading (and ever holding) the shapes we cannot redistribute, and needs no GDAL. |
| Geometries | **None.** No `features/` directory; no CRS, dissolve, or simplify steps. |
| Storage | `wdpa/labels.tsv` + `wdpa/build.json`, plain commit (tiny). |

## Build pipeline (`scripts/wdpa/build.py`)

Follows the standard per-gazetteer driver contract, minus all geometry steps
(download → parse → labels → manifest; idempotent, `--force` re-downloads). No
`--crs` flag — there are no shapes to reproject.

```
download WDPA_<Mon><Year>_Public_csv.zip  → sources/wdpa/   (cached, gitignored)
  │  unzip → single combined CSV (WDPA + WDOECM, Polygon + Point rows)
  ▼
parse with stdlib csv → dedup by SITE_ID → (SITE_ID, NAME_ENG) rows
  ▼
wdpa/labels.tsv     SITE_ID → NAME_ENG   (committed; UTF-8, tab-delimited, no header)
wdpa/build.json     provenance via common/manifest.py
```

No `sources/` shapes, no `work/` intermediate geojson, no `features/`, no GDAL.

### Details (verified against the Jun2026 release)

- **Source URL:** Protected Planet's current CSV release, CloudFront-served:
  `https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_<Mon><Year>_Public_csv.zip`
  (e.g. `WDPA_Jun2026_Public_csv.zip`, ~24 MB). Construct the `<Mon><Year>` token
  (`%b%Y`, e.g. `Jun2026`) from the current month; allow a `--month` override since
  the release rolls monthly. `common/download.py` caches and records the `SourceRecord`.
- **Archive layout:** the zip contains one combined `WDPA_<Mon><Year>_Public_csv.csv`
  (~152 MB, both `TYPE=Polygon` and `TYPE=Point` rows; WDPA + WDOECM together), a
  `WDPA_sources_<Mon><Year>.csv`, and multilingual PDF manuals. Only the main CSV is read.
- **Encoding:** UTF-8 with BOM — read with `encoding="utf-8-sig"`.
- **Columns (2026 schema):** the id is **`SITE_ID`** (integer; the WDPAID, e.g.
  `1` → protectedplanet.net/1) and the label is **`NAME_ENG`** (English name;
  populated for every row in the release — 0 empties). `SITE_PID` is the parcel id,
  `NAME` is the local-language name (not used).
  > Note: this 2026 release renamed the historical `WDPAID`/`WDPA_PID`/`NAME` columns
  > to `SITE_ID`/`SITE_PID`/`NAME_ENG`. The build reads the new names.
- **Dedup:** a `SITE_ID` appears in multiple parcel rows (314,622 rows →
  **312,799 distinct SITE_IDs**). Collapse to one row per SITE_ID (first NAME_ENG
  wins). Skip rows with a missing/empty SITE_ID or NAME_ENG.
- **Id sanity:** every emitted id must match `^[0-9]+$` (the deployed pattern;
  all SITE_IDs in the release are numeric); assert this in-build so a malformed source
  row fails loudly.
- **build.json:** standard manifest fields (`built_at`, `feature_count` = 0,
  `label_count`, the CSV `SourceRecord`, tool versions, git HEAD). Geometry-specific
  fields (`crs`, `simplify_tolerance`) are omitted via a labels-only path in
  `common/manifest.py` (make its `config` argument optional).

## Other files touched

The backend `Gazetteer.WDPA` enum entry is **already deployed** — no backend work.

- **`scripts/test_id_patterns.py`** — the test currently only discovers prefixes
  that have a `features/` subdir and requires labels.tsv ids to match feature
  filenames 1:1. Add a `LABELS_ONLY = {"wdpa"}` set: include such prefixes in
  discovery (they have `labels.tsv` but no `features/`), and in `check_prefix` skip
  the label-vs-feature coverage check for them while still validating their label
  ids against the vocab pattern.
- **viewer `index.html`** — add `wdpa` as a **labels-only card** so the gazetteer is
  discoverable and its titles are searchable, but with **no map overlay**:
  - Register `wdpa` in the `init()` `order` array, `PRETTY_NAME`, and `PREFIX_ORDER`
    (for a card swatch). Do **not** add it to `GLOBAL_PRESET` (nothing to draw).
  - Add a viewer-side `NO_GEOMETRY = new Set(["wdpa"])`.
  - The card body shows a **license notice** banner above the filter box, e.g.
    "⚠ Areas are not displayed — the WDPA license prohibits redistributing geometry.
    Titles are searchable below; each links to its Protected Planet page."
  - Rows for a `NO_GEOMETRY` prefix do **not** call `addFeature` (which would 404 on
    `features/<id>.geojson`). Instead each row links to
    `https://www.protectedplanet.net/<WDPAID>` (open in a new tab) — lookup + advertise
    + send the user to the authoritative source for the shape.
  - `buildCard`/`metaHTML`: for a `NO_GEOMETRY` prefix, show the **label count** (not
    "features") in the header and omit the CRS/simplify line (use `label_count` from
    `build.json`; `feature_count` is 0).
  - Note: `wdpa/labels.tsv` (~300k rows) is the largest the viewer loads; acceptable
    (it loads, caps the rendered list at 500, and filters client-side as for mrgid).
- **`README.md`** + **`scripts/README.md`** — add the `wdpa` row to the gazetteer
  tables (mark it labels-only / no geometry) and note the exception to the
  "geometries always live here for every gazetteer" statement. Update the on-disk
  layout to show `wdpa/` with `labels.tsv` + `build.json` only.
- **`ATTRIBUTIONS.md`** — WDPA citation: "UNEP-WCMC and IUCN (`<Year>`), Protected
  Planet: The World Database on Protected Areas (WDPA), `<Month/Year>` release,
  Cambridge, UK: UNEP-WCMC and IUCN. Available at: www.protectedplanet.net." Note
  labels-only redistribution (names, no geometry) per WDPA T&C.

## Risks

- **Source URL stability:** the CloudFront CSV path / month token may change month
  to month. Mitigate with the `--month` override and a clear download error.
- **Licensing:** even labels (names) are WDPA-derived; we redistribute only the
  WDPAID→name mapping, no geometry, with attribution. If the maintainer learns even
  name redistribution is restricted, revisit.

## Out of scope

- Any geometry (`features/`), CRS/dissolve/simplify, **viewer map overlay** (license).
  The viewer still gets a searchable, link-out labels-only card.
- Per-parcel (`WDPA_PID`) granularity.
- WDPA REST API ingestion (CSV download chosen).
- Backend enum changes (already deployed).
