# `gi` — Global Islands gazetteer

**Date:** 2026-09-08
**Status:** Implemented
**Prefix:** `gi`

## Goal

`col-gazetteers` has no island gazetteer. Land coverage comes from `iso`
(countries/subdivisions), `tdwg` (botanical countries) and `teow`/`realm`
(ecoregions) — none resolve an individual island, even though island endemism
makes islands one of the most-cited area types in CoL distributions.

**Global Islands** (USGS + Esri + UNEP-WCMC) fills that gap: 369,401 polygons
derived from a 30 m Global Shoreline Vector built from 2014 Landsat composites.
Unlike WDPA it is **public domain** — the FGDC metadata records access and use
constraints as "none" — so geometry ships.

Outcome: `gi:{ALL_Uniq}` resolves to a named island polygon on the same on-disk
contract as every other gazetteer.

## Decisions

| Question | Decision |
|---|---|
| **Scope** | The **`BigIslands` layer only** (>1 km²), restricted to islands with a USGS name → **15,139 features**. |
| **Smaller size classes** | **Excluded.** Sub-1 km² islands are below the granularity a regional checklist works at — they matter for specimen occurrences, not for a checklist of a region — and their names are mostly unusable (below). |
| **Mainlands** | **Excluded.** No name field and no `ALL_Uniq`; would need synthetic ids and hand-typed labels. |
| **Id** | **`ALL_Uniq`** — upstream-documented as unique across all four size classes. Not `USGS_ISID` (per-polygon, min value `0`). Pattern `^[0-9]+$`. |
| **Label** | **`Name_USGSO` only** (see "The naming trap"). The UNEP-WCMC name columns are not used at all. |
| **Source** | One 818 MB zip, direct and scriptable: `https://www.arcgis.com/sharing/rest/content/items/885a860af66d4833887dcce735a521a7/data`. |
| **Simplify** | **`0.002°`**, overriding the repo's `0.005°` default. |
| **Storage** | Plain commit: 22 MB of JSON in 15,139 files (~71 MB allocated). |
| **Backend link** | No `areaLinkTemplate` — the Global Island Explorer has no documented per-island deep link. Same as `teow`/`longhurst`; irrelevant since we ship geometry. |

## The naming trap

This is the finding that shaped the spec, and the reason the gazetteer is
15k features rather than 112k.

**"Has a non-blank name field" is not "has its own name."** Two independent
problems, neither visible to an `IS NOT NULL AND <> ''` test:

1. **`UNNAMED` is a literal value.** 6,659 big islands carry the string
   `UNNAMED` in `Name_USGSO`. Blank names also appear as a *single space*, never
   as NULL. The build therefore excludes all three of `''`, `' '` and
   `'UNNAMED'` explicitly.

2. **The UNEP-WCMC names were transferred by proximity, not identity.** The
   `NEAR_FID` / `NEAR_DIST` / `Meaning_AL` columns are the residue of a
   nearest-neighbour join between the old WCMC island layer and the new GSV
   polygons, and it leaked badly. Accepting any non-blank name gives 2,768
   features labelled "Baffin Island", 805 "Iceland", 657 "Cuba" — islets
   inheriting the name of the large island beside them — and only **31.5 %** of
   features end up uniquely named.

The dataset's own join-quality flag does **not** separate good from bad:
`Meaning_AL` marks only 43 % of known-bad cases as non-intersecting, because the
coarse WCMC polygons genuinely overlap the islets near them. Even among big
islands, WCMC-sourced names include 85 separate islands called "New Guinea" and
81 called "Borneo".

What works is the name's *source*. `Name_USGSO` was compiled per island by USGS
against Google Earth, and holds up at every size class:

| Layer | any non-blank name (minus `UNNAMED`) | `Name_USGSO` only |
|---|---|---|
| `BigIslands` | 16,639 feats — 75.1 % uniquely named | 15,139 — **78.1 %** |
| `SmallIslands` | 72,030 — 36.9 % | 12,884 — 80.9 % |
| `VerySmallIslands` | 18,500 — 13.5 % | 61 — 100 % |

Shipped result: 15,139 features over 12,774 distinct names, **92.6 % of names
used exactly once**. The residual repeats are genuine — multi-island delta
complexes (116 islands in the Amazon Delta, 96 in the Orinoco) and real island
names that recur worldwide ("Andros", "Long").

The small classes were dropped on top of this for the reason in the Decisions
table: even USGS-named, they are dominated by generic names — 23 distinct
"Round Island"s, 10 "Redonda"s — which is precisely the ambiguity that makes a
label useless in a regional checklist. The known cost is a handful of
biodiversity-notable islets, e.g. Ball's Pyramid (0.246 km², `ALL_Uniq` 273388).
Restoring them is one predicate change plus a rebuild.

## Source

```
https://www.arcgis.com/sharing/rest/content/items/885a860af66d4833887dcce735a521a7/data
  → GlbIslands.gdb.zip, 817,969,164 bytes, content-type application/zip, no auth
  → item page (human reference):
    https://www.arcgis.com/home/item.html?id=885a860af66d4833887dcce735a521a7
```

Owner `rsayre@usgs.gov_USGS`. Unzips to a 961 MB `GlbIslands.gdb`, EPSG:4326,
whose four layer counts match the live FeatureServer exactly (22,471 / 269,391 /
77,534 / 8) — same v3 data.

**Unzipped into `sources/gi/` rather than read through `/vsizip/`.** OpenFileGDB
does random seeks across `.gdbtable` files; through a deflated zip member that
means repeated decompression. A deliberate departure from the `teow` idiom.

*(The ScienceBase `.mpk` at doi:10.5066/P91ZCSGM is unusable for a build: 1.5 GB
behind an S3 `s3DownloadRequestPageUri` request flow with no direct URL. It
remains the citation.)*

**GDAL note:** this toolchain (3.8.5) has **no SQLite dialect**
(`OGR2SQLITE_Setup() failed due to sqlite3_api == nullptr`), so all filtering
uses OGRSQL — which has no `TRIM()`. The WDPA spec assumed the SQLite dialect
was available; it is not safe to.

## Simplification

`0.002°` (~220 m), overriding the repo default. Measured over `BigIslands`,
with the share of features left with ≤4 coordinates in brackets:

| `0.0005°` | `0.001°` | **`0.002°`** | `0.005°` (default) |
|---|---|---|---|
| 92.5 MB (0 %) | 52.5 MB (0 %) | **31.5 MB (0.1 %)** | 17.2 MB (**6.4 %**) |

At the repo default one big island in sixteen collapses to a triangle. `0.002°`
fixes that for 14 MB. (Figures are for all 22,047 name-bearing features; the
shipped subset is 15,139 / 22 MB.)

## Build pipeline — `scripts/gi/build.py`

```
download GlbIslands.gdb.zip → sources/gi/        (cached, gitignored, SourceRecord)
  │  unzip → sources/gi/GlbIslands.gdb
  ▼
ogr2ogr -t_srs EPSG:4326 -simplify 0.002 -makevalid -skipfailures
        -where <USGS-named> -select ALL_Uniq,Name_USGSO,Area_Geode,Plate  BigIslands
  ▼
work/gi/bigislands.geojson  → tidy properties in Python
  ▼
gi/features/<ALL_Uniq>.geojson · gi/labels.tsv · gi/build.json
  + work/gi/unnamed-big-islands.tsv   (7,332 rows, gitignored)
```

Standard `teow` shape: `download()` → `to_geojson()` → `split_features()`.
Feature properties are reduced to `ALL_Uniq`, `name`, `Area_Geode`, `Plate`
before splitting — `split_features` writes every property into every file.

Two guards, because `-skipfailures` drops silently and 15k features are too many
to eyeball: the named count is checked against `EXPECTED_NAMED`, and the number
of files written must equal the number of features read.

### The skipped-islands report

`work/gi/unnamed-big-islands.tsv`: all **7,332** big islands with no usable USGS
name (6,659 of them the literal `UNNAMED`), with area, plate and id, largest
first. The largest is **52,223 km²** — bigger than Denmark — and a contiguous
`ALL_Uniq` block of them are unattributed Antarctic islands. Gitignored; promote
to a committed `gi/unnamed.tsv` (the `iso/sources.tsv` precedent) if it should
become diffable.

## Shared-helper changes

- **`common/ogr.py`, `split_features`** — `-makevalid` can emit a
  `GeometryCollection` (polygons plus stray lines/points). The function only
  normalised geometry when a *duplicate id* triggered a merge, so a
  non-duplicate GeometryCollection was written to disk verbatim, breaking the
  "one polygonal Feature per file" contract. Now every geometry is collapsed to
  its polygonal parts on the first-write path, and a feature left with nothing
  polygonal is dropped and logged. **This was a live bug**: rebuilding `teow`
  repaired 5 already-committed features, with the polygon coordinates
  byte-identical and only the stray lines/points removed. `fao` (5), `iho` (4),
  `tdwg` (5) and `mrgid` (70) still carry committed GeometryCollections and will
  self-repair on their next rebuild.
- **`common/ogr.py`, `to_geojson`** — new `select` parameter (`ogr2ogr -select`),
  so wide sources don't pay for unused columns once per feature.
- **`scripts/test_id_patterns.py`** — an `EXTENSION_PREFIXES` entry previously
  skipped *all* checks, including the labels-vs-features coverage check, which
  needs no backend. Extension prefixes are now checked for coverage and against
  a locally declared intended pattern (`EXTENSION_PATTERNS`), so a malformed id
  fails here rather than only after the enum is deployed.

## Other files touched

- `index.html` — `gi` in `PREFIX_ORDER`, `PRETTY_NAME` and the `init()` `order`
  array. Not in `NO_GEOMETRY`; **not** in `GLOBAL_PRESET`, since islands overlap
  the `iso`/`tdwg` land layers — the same reason `iho` is omitted.
- `.github/workflows/build.yml` — `gi` added to the build loop and the
  changed-prefix filter. **`wdpa` was missing from both** and was added too.
- `README.md`, `scripts/README.md`, `ATTRIBUTIONS.md` — gazetteer table, source
  map, licence/citation.
- `scripts/pyproject.toml` — the package include lists were stale (missing
  `teow`, `wdpa`); all three added.

## Backend — done

`Gazetteer.GI` is deployed to prod and dev: title "Global Islands", link
`https://apps.usgs.gov/glbeco/gie.html`, no `areaLinkTemplate`, `pattern`
`^[0-9]+$`, `caseSensitive` false, `areaClass` `GenericArea`.

With no `areaLinkTemplate`, `getAreaLink()` falls back to
`https://api.checklistbank.org/vocab/area/gi:{id}` — the CLB GeoJSON endpoint,
which is the right target since the geometry ships here.

`gi` has accordingly been removed from `EXTENSION_PREFIXES` in
`test_id_patterns.py`, which now validates all 15,139 ids against the live
pattern (verified against both api.checklistbank.org and
api.dev.checklistbank.org).

## Risks

- **Single-source availability.** One AGOL item, no scriptable archival
  fallback. `sources/gi/` is gitignored, so a rebuild after the item disappears
  needs a human. Item id and DOI are recorded in `ATTRIBUTIONS.md`.
- **Weekly CI.** An 818 MB download plus ~40 s of conversion. v3 is a static
  release; if the download cost bites, drop `gi` from the weekly loop.
- **Coverage.** 7,332 big islands (a third of the layer) have no name and are
  not shipped. If CoL data references one, it will not resolve.

## Out of scope

- Size classes below 1 km²; continental mainlands; unnamed islands.
- UNEP-WCMC names, including as an alternate-name column.
- Reconciling `gi` ids against `mrgid` or `iso` island geometries.
- Backend enum deployment.
