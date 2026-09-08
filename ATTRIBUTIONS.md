# Attributions and licenses

The **build scripts and metadata** in this repo (everything under `scripts/`, plus `README.md`, `ATTRIBUTIONS.md`, `index.html`, etc.) are licensed under **Apache 2.0**.

The **generated geometry data** (`*/features/*.geojson`, `*/labels.tsv`, `*/build.json`) is derived from third-party sources. Each source's licence and attribution requirement is given below; consumers of this repo must comply with the licence of the upstream they care about. The exact upstream artifact, URL, version and content hashes used for each build are recorded per-gazetteer in `<prefix>/build.json`.

## Viewer basemap

`index.html` renders the gazetteer polygons over the GBIF basemap tile service
(<https://tile.gbif.org/ui/>), style `gbif-geyser-en`, which needs no API key.

- **Tiles:** GBIF, generated with [gbif/openmaptiles](https://github.com/gbif/openmaptiles)
- **Map data:** © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors ([ODbL](https://opendatacommons.org/licenses/odbl/)), packaged to the [OpenMapTiles](https://openmaptiles.org/) schema; low zoom levels use [Natural Earth](https://www.naturalearthdata.com/) (public domain)
- **Attribution shown in the viewer:** "© OpenStreetMap contributors, OpenMapTiles · tiles by GBIF"

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

### `gi` — Global Islands (USGS / Esri / UNEP-WCMC)

- **Upstream:** U.S. Geological Survey, Esri and UNEP-WCMC. A three-party product: USGS/Esri produced the 30 m Global Shoreline Vector (semi-automated interpretation of 2014 Landsat composites) and the island polygons derived from it; UNEP-WCMC contributed the earlier island layer that most of the names come from — the attribute table documents `NAME_wcmcI` / `NAME_LOCAL` as "from the WCMC Island Conservation data layer", and `NEAR_FID` / `NEAR_DIST` / `Meaning_AL` are residue of the spatial join used to reconcile the two. (The older *Global Islands Database*, `ID_GID`, is the UNEP-WCMC predecessor, not this product.)
- **Source:** the Global Islands file geodatabase, ArcGIS Online item [`885a860af66d4833887dcce735a521a7`](https://www.arcgis.com/home/item.html?id=885a860af66d4833887dcce735a521a7) (`GlbIslands.gdb.zip`, v3). Exact URL, size and hashes are in `gi/build.json`. The equivalent USGS data release is <https://doi.org/10.5066/P91ZCSGM>, but its 1.5 GB map package is only obtainable through a manual ScienceBase download request, so the build uses the ArcGIS item.
- **Licence:** USGS **public domain** — the FGDC metadata for the data release records both access and use constraints as "none". The GEO Knowledge Hub package records it as [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/). Either way, redistribution with attribution is permitted, so unlike `wdpa` the geometries ship here.
- **Required citation:** *"Sayre, R., 2023, Global Islands: U.S. Geological Survey data release, https://doi.org/10.5066/P91ZCSGM."*
- **Primary publication:** Sayre, R., Noble, S., Hamann, S., Smith, R., Wright, D., Breyer, S., Butler, K., Van Graafeiland, K., Frye, C., Karagulle, D., Hopkins, D., Stephens, D., Kelly, K., Basher, Z., et al. (2018). *A new 30 meter resolution global shoreline vector and associated global islands database for the development of standardized ecological coastal units.* **Journal of Operational Oceanography** 12 (sup2): S47–S56. <https://doi.org/10.1080/1755876X.2018.1529714>
- **Use here:** the `BigIslands` layer (>1 km²) restricted to islands carrying a USGS name — 15,139 of 369,396 polygons — keyed by `ALL_Uniq`. The smaller size classes and the continental mainlands are excluded, and only `Name_USGSO` is used as the label: the UNEP-WCMC name columns were attached by a nearest-neighbour spatial join and frequently name a neighbouring island rather than the polygon itself. Cite the data release, not the Global Island Explorer, which is the browser app over it.

### `wdpa` — World Database on Protected Areas (UNEP-WCMC & IUCN) — labels only

- **Upstream:** UNEP-WCMC and IUCN, Protected Planet — the World Database on Protected Areas (WDPA) and the World Database on OECMs (WDOECM).
- **Source:** the monthly global public release in CSV form, downloaded via the Protected Planet CloudFront mirror; the exact URL, month token and hashes are in `wdpa/build.json`.
- **Licence:** [WDPA Terms & Conditions](https://www.protectedplanet.net/c/terms-and-conditions) — **redistribution of the data is restricted.** Because of this, **this repo ships only the `wdpa/labels.tsv` name lookup (`SITE_ID → NAME_ENG`); no geometries are redistributed** (`wdpa/features/` does not exist, and the backend does not serve WDPA GeoJSON). For protected-area boundaries, refer people to <https://www.protectedplanet.net/>.
- **Required citation:** *"UNEP-WCMC and IUCN (`<year>`), Protected Planet: The World Database on Protected Areas (WDPA) and World Database on OECMs (WDOECM), `<month>/<year>`, Cambridge, UK: UNEP-WCMC and IUCN. Available at: www.protectedplanet.net."* (the release month/year is the `sources[].upstream_version` field in `wdpa/build.json`.)

## Backend cross-references

Vocabulary / enum definitions are owned by the ChecklistBank backend:

- Gazetteer enum: [`life.catalogue.api.vocab.area.Gazetteer`](https://github.com/CatalogueOfLife/backend/blob/master/api/src/main/java/life/catalogue/api/vocab/area/Gazetteer.java)
- Backend vocab API (id patterns, country enum): <https://api.checklistbank.org/vocab/gazetteer> and <https://api.checklistbank.org/vocab/country>
