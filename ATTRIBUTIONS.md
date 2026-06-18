# Attributions and licenses

The **build scripts and metadata** in this repo (everything under `scripts/`, plus `README.md`, `ATTRIBUTIONS.md`, `index.html`, etc.) are licensed under **Apache 2.0**.

The **generated geometry data** (`*/features/*.geojson`, `*/labels.tsv`, `*/build.json`) is derived from third-party sources. Each source's licence and attribution requirement is given below; consumers of this repo must comply with the licence of the upstream they care about. The exact upstream artifact, URL, version and content hashes used for each build are recorded per-gazetteer in `<prefix>/build.json`.

## Per-source attributions

### `fao`, `iho`, `mrgid`, `longhurst` — VLIZ / MarineRegions

- **Upstream:** Flanders Marine Institute (VLIZ), MarineRegions.org
- **Endpoint:** `https://geo.vliz.be/geoserver/MarineRegions/ows` (public OGC WFS service)
- **Licence:** [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see <https://www.marineregions.org/disclaimer.php>
- **Required citation:** *"Flanders Marine Institute (VLIZ). MarineRegions.org. Consulted on YYYY-MM-DD."* (the `built_at` field in each `<prefix>/build.json` is the consultation date.)
- **Per-layer references:**
  - FAO Major Fishing Areas: <https://www.marineregions.org/sources.php#fao>
  - IHO Sea Areas v3 (S-23): <https://www.marineregions.org/sources.php#iho>
  - Longhurst Biogeographical Provinces v4: <https://www.marineregions.org/sources.php#longhurst>
  - MRGID is a union of VLIZ's `eez`, `lme`, `iho`, `fao`, `longhurst`, `high_seas`, `ecs`, `ices_areas`, `ices_ecoregions`, `arcticmarineareas`, and `gazetteer_polygon` WFS layers. The full source list is in `mrgid/build.json`.

### `tdwg` — TDWG World Geographical Scheme for Recording Plant Distributions (WGSRPD)

- **Upstream:** Biodiversity Information Standards (TDWG)
- **Source:** [tdwg/wgsrpd](https://github.com/tdwg/wgsrpd) on GitHub — `geojson/level{1,2,3,4}.geojson`
- **Standard:** *World Geographical Scheme for Recording Plant Distributions* (Brummitt 2001), TDWG Standard <http://www.tdwg.org/standards/109>
- **Licence:** [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- **Required citation:** *"Brummitt, R. K. (2001). World Geographical Scheme for Recording Plant Distributions. Edition 2. TDWG, Pittsburgh."*

### `iso` — ISO 3166-1 + ISO 3166-2 (Natural Earth)

- **Upstream:** Natural Earth, by Tom Patterson and Nathaniel Vaughn Kelso
- **Source:** `https://naciscdn.org/naturalearth/10m/cultural/`
  - `ne_10m_admin_0_countries.zip` (3166-1 alpha-2)
  - `ne_10m_admin_0_map_subunits.zip` (3166-1 alpha-2, fills NE's "ISO_A2 = -99" gaps via `ISO_A2_EH`)
  - `ne_10m_admin_1_states_provinces.zip` (3166-2)
- **Licence:** [Public domain](https://www.naturalearthdata.com/about/terms-of-use/) (CC0-equivalent)
- **Attribution:** Not required; the project asks reusers to credit "Made with Natural Earth" when convenient.

### `realm`, `teow` — RESOLVE Ecoregions 2017 (Dinerstein et al.)

- **Upstream:** RESOLVE (`https://storage.googleapis.com/teow2016/Ecoregions2017.zip`)
- **Citation:** Dinerstein, E., Olson, D., Joshi, A. et al. (2017). *An Ecoregion-Based Approach to Protecting Half the Terrestrial Realm.* **BioScience** 67 (6): 534–545. <https://doi.org/10.1093/biosci/bix014>
- **Lineage:** Update of Olson, D.M., et al. (2001). *Terrestrial Ecoregions of the World: A New Map of Life on Earth.* **BioScience** 51 (11): 933–938.
- **Licence:** [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see <https://ecoregions.appspot.com/>
- **Use here:** `realm` features dissolve all ecoregions by `REALM`; `teow` features keep all 847 ecoregions individually keyed by `ECO_ID`.

### `wdpa` — World Database on Protected Areas (UNEP-WCMC & IUCN) — labels only

- **Upstream:** UNEP-WCMC and IUCN, Protected Planet — the World Database on Protected Areas (WDPA) and the World Database on OECMs (WDOECM).
- **Source:** the monthly global public release in CSV form, downloaded via the Protected Planet CloudFront mirror; the exact URL, month token and hashes are in `wdpa/build.json`.
- **Licence:** [WDPA Terms & Conditions](https://www.protectedplanet.net/c/terms-and-conditions) — **redistribution of the data is restricted.** Because of this, **this repo ships only the `wdpa/labels.tsv` name lookup (`SITE_ID → NAME_ENG`); no geometries are redistributed** (`wdpa/features/` does not exist, and the backend does not serve WDPA GeoJSON). For protected-area boundaries, refer people to <https://www.protectedplanet.net/>.
- **Required citation:** *"UNEP-WCMC and IUCN (`<year>`), Protected Planet: The World Database on Protected Areas (WDPA) and World Database on OECMs (WDOECM), `<month>/<year>`, Cambridge, UK: UNEP-WCMC and IUCN. Available at: www.protectedplanet.net."* (the release month/year is the `sources[].upstream_version` field in `wdpa/build.json`.)

## Backend cross-references

Vocabulary / enum definitions are owned by the ChecklistBank backend:

- Gazetteer enum: [`life.catalogue.api.vocab.area.Gazetteer`](https://github.com/CatalogueOfLife/backend/blob/master/api/src/main/java/life/catalogue/api/vocab/area/Gazetteer.java)
- Backend vocab API (id patterns, country enum): <https://api.checklistbank.org/vocab/gazetteer> and <https://api.checklistbank.org/vocab/country>
