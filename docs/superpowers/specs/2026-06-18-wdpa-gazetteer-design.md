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
  │  unzip → attribute CSV(s) carrying WDPAID, NAME for poly + point records
  ▼
parse with stdlib csv → dedup by WDPAID → (WDPAID, NAME) rows
  ▼
wdpa/labels.tsv     WDPAID → NAME   (committed; UTF-8, tab-delimited, no header)
wdpa/build.json     provenance via common/manifest.py
```

No `sources/` shapes, no `work/` intermediate geojson, no `features/`, no GDAL.

### Details

- **Source URL:** Protected Planet's current CSV release, CloudFront-served
  (e.g. `https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_<Mon><Year>_Public_csv.zip`).
  Construct the `<Mon><Year>` token from the current month; allow a `--month`
  override. `common/download.py` caches and records the `SourceRecord`.
- **Name field:** use `NAME` (English name). (`ORIG_NAME` is the original-language
  name; not used.)
- **Dedup:** a WDPAID can appear in many parcel rows and across the poly + point
  CSVs — collapse to one row per WDPAID (first NAME wins; names are consistent
  across a WDPAID's parcels). Skip rows with a missing/empty WDPAID or NAME.
- **Id sanity:** every emitted id must match `^[0-9]+$` (the deployed pattern);
  assert this in-build so a malformed source row fails loudly.
- **build.json:** standard manifest fields (`built_at`, `feature_count` = 0 /
  `label_count`, the CSV `SourceRecord`, tool versions, git HEAD). Geometry-specific
  fields (`crs`, `simplify_tolerance`) are omitted or null since there are no shapes.

## Other files touched

The backend `Gazetteer.WDPA` enum entry is **already deployed** — no backend work.

- **`scripts/test_id_patterns.py`** — the test currently only discovers prefixes
  that have a `features/` subdir and requires labels.tsv ids to match feature
  filenames 1:1. Add a `LABELS_ONLY = {"wdpa"}` set: include such prefixes in
  discovery (they have `labels.tsv` but no `features/`), and in `check_prefix` skip
  the label-vs-feature coverage check for them while still validating their label
  ids against the vocab pattern.
- **viewer `index.html`** — **not** added to the map (no geometries to overlay).
  Optionally note in the intro paragraph that `wdpa` is labels-only; otherwise leave
  the viewer untouched.
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

- Any geometry (`features/`), CRS/dissolve/simplify, viewer overlay (license).
- Per-parcel (`WDPA_PID`) granularity.
- WDPA REST API ingestion (CSV download chosen).
- Backend enum changes (already deployed).
