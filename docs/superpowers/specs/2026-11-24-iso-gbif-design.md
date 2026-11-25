# `iso` — rebuilt on GBIF's country areas, with an opt-in EEZ shape

**Date:** 2026-11-24
**Status:** Approved
**Prefix:** `iso`
**Branch:** `iso-gbif`

## Goal

The Catalogue of Life should define countries the way GBIF does, so that `iso:NO` means the same area in ChecklistBank as in GBIF's occurrence processing. GBIF uses the VLIZ union of country boundaries and EEZs (v4, 2024), with its own changes on top. Marine and fisheries users also need a country's sea area, not only its land.

Outcome:
- **Two shapes per ISO 3166-1 code, with the same ids:**
  - land, the default;
  - land + EEZ, opt in through the API.
- **Subdivisions (ISO 3166-2) rebuilt, mostly from OpenStreetMap,** and fitted into GBIF's countries without losing ids.

The current `iso` tree and `scripts/iso/` are wiped on this branch and rebuilt from scratch.

## Decisions

| Question | Decision |
|---|---|
| **Country areas** | VLIZ *Marine and land zones: the union of world country boundaries and EEZs*, v4 (ESRI countries + EEZ v12): the very file GBIF imports, `EEZ_land_union_v4_202410.zip` from GBIF's `MapDataMirror`. Not the WFS layer `MarineRegions:eez_land`, though the spike showed it holds the same 328 rows. |
| **EEZ water** | `eez_v12.gpkg`, also from GBIF's `MapDataMirror`, which GBIF imports in `import_mr_eez()`. |
| **Getting the two files** | GBIF's mirror is reachable only from GBIF's network, so the build looks in three places, in order. (1) `sources/iso/`, git-ignored, where someone on the VPN puts them by hand. (2) The mirror itself, when the build runs on the VPN. (3) A release of the private repo `CatalogueOfLife/gazetteer-sources`, for CI: the workflow downloads the release files with `gh release download`, using a read-only token kept as a repository secret. A GitHub App is preferred over a personal token, which expires. The files are never published from a public place. When GBIF switches to a new file, which shows in `import.sh`, someone uploads it to a new release and the build pins that release tag. |
| **GBIF's changes** | Replay `import_political()` of [`gbif/geocode` `database/import.sh`](https://github.com/gbif/geocode/blob/master/database/import.sh), step by step and in order, in Python. Its OSM relations come from the OSM API, as GBIF does. Pin the replayed commit (`6a578ecefde0`, 2026-11-06). Warn on rebuild when `import_political()` or the mirrored file names have changed upstream since that commit. |
| **Which code an area counts for** | Every code among `iso_ter1..3`, mapped to alpha-2 through `api.gbif.org/v1/enumeration/country` (+ `XKX`→`XK`), as GBIF's geocoder does. So overlapping claims count for every claimant. |
| **Land + EEZ shape** | Union of all rows that name the code. |
| **Land shape** | The same rows minus the EEZ v12 water polygons (full resolution, before simplifying). Islands that only a subdivision source has get added, see "Fitting subdivisions". Taking ESRI countries + EEZ v12 separately was rejected: GBIF's changes are defined on the union, and subtracting from the union guarantees land ⊆ land + EEZ. |
| **Subdivision ids** | Never fewer than before. Each build takes the last committed `iso/labels.tsv` as its floor. New ids come only from Wikidata, as today: every ISO 3166-2 code with a `P300` statement, current or withdrawn, that matches the backend pattern `^[A-Z]{2}(-[A-Z0-9]{1,6})?$`. OSM `ISO3166-2` tags are a shape source, never an id source: a code only OSM has waits until Wikidata has it too. |
| **Subdivision shapes** | First source that has the id, in this order: (1) OSM admin relation tagged with exactly that `ISO3166-2`; (2) OSM relation linked from the Wikidata item (`P402`); (3) Natural Earth `admin_1`; (4) geoBoundaries, by `shapeISO`, else by name at district level (ADM2 before ADM1, never ADM3); (5) a union of known parts (e.g. `GB-GBN`, `GB-EAW`, `CS-SR` → `RS`); (6) a placeholder circle, as today. |
| **Fitting subdivisions** | Clip to the GBIF land of the ISO parent plus the territories ISO also codes under it (e.g. `FR` + `GF`, `RS` + `XK`). If that keeps less than 10% of the shape, clip to the parent's land + EEZ instead, and add what is kept to the parent's land shape. This affects atolls and islands that ESRI's coastline misses. A clipped placeholder circle is kept however small. If a source's shape keeps less than 10% even then, try the next source in the list. If none fits, keep the best shape unclipped and mark it `conflict` in `sources.tsv`, with the GBIF code that holds the area. Likoma (`MW-LK`), which ESRI places in Mozambique, is the known case. Subdivisions are not stretched to fill gaps: they need not tile their country. |
| **Dual-coded ids** | An ISO 3166-2 code that is also an ISO 3166-1 code becomes a symlink to the country, in both sets: `FR-GF`→`GF`, `US-PR`→`PR`, `NL-SX`→`SX`, `CN-TW`→`TW`, `FI-01`→`AX`, … |
| **Historic codes** | `YU` and `AN` are dissolved from their successors, and `TP`→`TL` is a symlink, all in both sets. Withdrawn subdivision codes replaced 1:1 become symlinks to their successor, as today. |
| **On disk** | `iso/features/<id>.geojson` is the land shape, as today. New: `iso/eez/features/<id>.geojson` for countries and the symlinks to them, plus `iso/eez/overview.geojson`, `map.svg`, `map.png` and `map-thumb.png`. One `labels.tsv`, `sources.tsv` and `build.json` for both. |
| **API** | Backend, separate PR: `GET /vocab/area/{prefix}:{id}` with `Accept: application/geo+json` takes `?variant=eez`. It serves `<prefix>/eez/features/<id>.geojson` and falls back to the land file when there is none (subdivisions, other gazetteers). `/vocab/gazetteer/{name}/overview` takes the same parameter. Named `variant` so other gazetteers can add their own later. |
| **Labels** | Ids already in the last committed `labels.tsv` keep their label from it. Fixing one is a deliberate edit, through `LABEL_OVERRIDES` as today. New ids take the Wikidata English label when they first appear, else OSM `name:en`, else `name`. |
| **Rebuild in CI** | `iso` moves out of `build.yml` into its own monthly workflow, `build-iso.yml`, modelled on `build-mrgid.yml`. Each run takes the two GBIF files and the OSM snapshot (next row) from the private release, and refetches Wikidata, Natural Earth and geoBoundaries. Nothing from the sources is committed or cached in the public repo, as for every other gazetteer. If any fetch fails after its retries, the build fails and nothing is committed, so the last good tree stays. |
| **OSM snapshot** (amended 2026-11-24) | The monthly build doesn't query Overpass. Overpass asks automated, high-volume users to run their own instance, and on the day of the first build it failed with 502 and 504 for hours. `scripts/iso/refresh_osm.py` fetches the boundaries from Overpass, however long that takes, into one compact file, `osm-snapshot.geojson.gz`, with one feature per relation (id, tags, lightly simplified geometry). The build reads only that file. A workflow started by hand or quarterly, `refresh-iso-osm.yml`, replaces it in the fixed release `osm-iso-snapshot` of the private repo; this needs the GitHub App to have write access there. |
| **Simplify** | Douglas–Peucker at the repository default of 0.005°, applied after all geometry work. |
| **Licences** | VLIZ: CC BY 4.0. OSM, including GBIF's OSM-based changes: ODbL; this now covers most subdivisions. Natural Earth: public domain. geoBoundaries: each country's own licence, from its metadata. Each feature names its source in `properties.source` and in `sources.tsv`. `iso/README.md` attributes them all. |

## What changes for users

These follow GBIF's definitions:

- **Overseas territories are no longer part of the mother country.** `FR` no longer includes French Guiana, Guadeloupe, Martinique, Réunion and Mayotte. `NO` no longer includes Svalbard, `NL` no longer includes the Caribbean Netherlands, and `IT` no longer includes the Vatican. All of them keep their own codes.
- **Crimea belongs to `UA`.** The Golan Heights belong to `SY` and the Ilemi Triangle to `SS`.
- **Disputed areas count for every claimant:**
  - Western Sahara: `EH` and `MA`
  - the Falklands and South Georgia: `FK`/`GS` and `AR`
  - Palestine: `PS` and `IL`
  - the southern Kurils: `RU` and `JP`
  - Hala'ib: `EG` and `SD`
  - Mayotte: `YT` and `KM`

  The land shapes of these countries overlap.
- **Coastlines and borders come from ESRI instead of Natural Earth.** The median land overlap with today's shapes is 96%.
- `XZ` (international waters) still has no shape.

## Evidence

A spike on 2026-11-24 compared today's `iso` with GBIF's areas and counted surviving ids. Survival means an id keeps at least 10% of its shape after fitting.

**Countries.** All 250 codes exist on both sides. The total land is 146.5 M km² in ours and 146.9 M km² in GBIF's. The spike used the WFS copy, because the mirror wasn't reachable. Every GBIF change applied cleanly to it. GBIF's real zip has the same 328 rows and columns, and every row GBIF's changes name, except one of the 14 joint regimes GBIF deletes (mrgid 21795). That row is missing from the WFS copy too, so its delete changes nothing.

**Subdivisions** (5,582 ids):

| Clip to | Sources | Kept | Real outline | Circle |
|---|---|---:|---:|---:|
| GBIF land | today's | 5,471 | 5,195 | 276 |
| GBIF land | + OSM, geoBoundaries, merges | 5,489 | 5,423 | 66 |
| GBIF land + EEZ | + OSM, geoBoundaries, merges | 5,575 | 5,495 | 80 |

The rule above (land first, land + EEZ as a fallback, placeholders always kept) keeps all 5,582 ids. About 5,495 have a real outline and about 80 are circles. The circles are mostly withdrawn codes: Greek prefectures, Prague's districts, Bahamian districts, Nepal's development regions and the Maldives' old provinces.

**Sources at a glance:**
- OSM has 5,154 relations tagged `ISO3166-2`, which covers 4,980 of our ids.
- It also tags 117 codes we don't list, such as new Algerian and Angolan provinces. They come in once Wikidata has them.
- Of today's 325 placeholders, 139 have an OSM relation with their code and 63 more are linked through Wikidata.

**Acceptance.**
- Hard requirement: no id is lost.
- Guide figures: at least 5,400 real outlines, and all 250 countries with both shapes.

## Left to the builder

- **Module layout** under `scripts/iso/`. The spike code to start from is in `work/iso-spike/`, which is git-ignored and kept locally:
  - `compare.py` replays GBIF's changes and extracts land;
  - `survive.py` does the fitting and counts survivors;
  - `osm_fetch.py` and `gb_match.py` fetch from OSM and match geoBoundaries.
- **How OSM is fetched.** The spike used Overpass per relation and per country, with retries across two public endpoints; the full set is about 5,000 relations. Within one run, responses can be kept under `sources/iso/`.
- **Which OSM relation wins when several carry the same code.** Prefer `boundary=administrative`, then the lowest `admin_level`.
- **The resolution categories in `sources.tsv`,** and the overview and map styling for the EEZ set.
- **Tests:**
  - no id from the previous `labels.tsv` disappears;
  - all 250 countries have both shapes;
  - land ⊆ land + EEZ;
  - every subdivision lies in its parent's land + EEZ, except those marked `conflict`;
  - the existing description and overview tests pass.

## Out of scope

- The backend change itself, in `CatalogueOfLife/backend` (`VocabResource`), and the ChecklistBank UI. Both follow once the gazetteer has landed.
- Gap filling, so that subdivisions tile their country.
- Reporting the `conflict` cases upstream to VLIZ and GBIF.
