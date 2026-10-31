# World Database on Protected Areas

National parks, nature reserves and marine sanctuaries are recorded in one global database: the World Database on Protected Areas (WDPA), run by UNEP-WCMC and IUCN and published on [Protected Planet](https://www.protectedplanet.net/). Together with its companion for other effective conservation measures (OECMs), it lists over 500,000 sites, each with a stable number. Species records use these numbers to say a species occurs in a particular park. So `wdpa:1000` is Bryce Canyon.

**This gazetteer has names only, and no shapes and no map.** The WDPA terms don't allow its data to be redistributed, so we ship only the list of ids and names. For the boundaries of a site, go to Protected Planet, for example <https://www.protectedplanet.net/1000>.

## Sources and attribution

- **UNEP-WCMC and IUCN** (2026). *Protected Planet: The World Database on Protected Areas (WDPA) and World Database on OECMs (WDOECM)*, October 2026. Cambridge, UK: UNEP-WCMC and IUCN. Available at: <https://www.protectedplanet.net/>. This is the required citation, with the release taken from `sources[].upstream_version` in [`build.json`](build.json), `Oct2026` for this build.

The use of the data is governed by the [WDPA Terms & Conditions](https://www.protectedplanet.net/c/terms-and-conditions).

## How the shapes were made

There are none. To stay within the terms, the build reads the monthly attribute-only CSV (`WDPA_<Mon><Year>_Public_csv.zip`, about 24 MB), not the geodatabase of several GB, so it never even downloads the shapes it can't redistribute. It needs no GDAL, only Python's standard library.

The CSV has separate rows for polygons, points and parcels of a site. The build collapses them to one label per site.

Because of this, there is no `features/` directory, no `overview.geojson` and no `map.svg`. The backend serves no GeoJSON for `wdpa`, and resolves `wdpa:{id}` to a name and a link to Protected Planet.

## Identifiers and names

Ids are `SITE_ID`, the integer WDPAID, unique per protected area. `SITE_PID`, the parcel id, isn't used. Labels come from `NAME_ENG`, the English name, not from `NAME`, the local-language one.

## Technical details

- **Labels:** 506,250 in the October 2026 release, 14 MB.
- **Build:** [`scripts/wdpa/build.py`](../scripts/wdpa/build.py). `--month Jun2026` overrides the release token. The download comes from UNEP-WCMC's CloudFront mirror.
- **Backend:** `Gazetteer.WDPA`, pattern `^[0-9]+$`, with the link template `https://www.protectedplanet.net/{id}`.

The reasoning is in the [design note](../docs/superpowers/specs/2026-06-18-wdpa-gazetteer-design.md).

## References

- UNEP-WCMC and IUCN, above.
