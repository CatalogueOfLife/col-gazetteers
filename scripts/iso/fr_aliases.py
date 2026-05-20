"""French (and a few other) ISO 3166-2 augmentations that Natural Earth
doesn't ship.

Four things live here:

1. `OVERSEAS_ALIASES` — overseas collectivities and special-status territories
   that ISO 3166 dual-codes: each is both an ISO 3166-1 alpha-2 country
   (PM, BL, MF, WF, PF, NC, TF; AS, GU, MP, UM, VI; AW, CW) *and* an
   ISO 3166-2 subdivision of its administering state (FR-PM, US-AS,
   NL-AW, ...). Natural Earth only ships them under the 2-letter country
   code, so we add the CC-XX form as a relative symlink to XX.geojson.

2. `CLIPPERTON_FEATURE` — Clipperton (FR-CP) has no upstream polygon in
   any of the Natural Earth layers (it's a 1.7 km² uninhabited atoll and
   has no 3166-1 code of its own since 1986). We synthesise a small
   bounding polygon around 10°18'N 109°13'W so the id resolves to *some*
   geometry rather than 404'ing.

3. `CURRENT_REGIONS` / `HISTORIC_REGIONS` — the 13 metropolitan régions
   (post-2016 reform) and the 22 pre-2016 régions, each defined by the
   set of INSEE département numbers it contains. The build dissolves
   French départements (FR-01, FR-2A, ...) into MultiPolygons keyed by
   these region codes; the merge-on-duplicate-id logic in
   `common.ogr.split_features` does the actual concatenation.

   We emit the historic codes with their *own* dissolved geometry rather
   than redirecting them to the merged successor region — a taxon
   distribution recorded as FR-V really meant the 1972-2015 Rhône-Alpes
   extent, which is strictly smaller than today's FR-ARA Auvergne-
   Rhône-Alpes.

4. `LABEL_OVERRIDES` — rewrites of the Natural Earth English label where it
   disagrees with the ISO 3166 / Wikipedia consensus (e.g. NE ships
   US-DC as "Washington", the city; ISO calls it "District of Columbia").
"""

from __future__ import annotations


OVERSEAS_ALIASES: dict[str, str] = {
    # France — overseas collectivities & special-status territories.
    "FR-PM": "PM",  # Saint-Pierre-et-Miquelon
    "FR-BL": "BL",  # Saint-Barthélemy
    "FR-MF": "MF",  # Saint-Martin (French part)
    "FR-WF": "WF",  # Wallis-et-Futuna
    "FR-PF": "PF",  # Polynésie française
    "FR-NC": "NC",  # Nouvelle-Calédonie
    "FR-TF": "TF",  # Terres australes et antarctiques françaises
    # United States — outlying areas (commonwealths, territories).
    "US-AS": "AS",  # American Samoa
    "US-GU": "GU",  # Guam
    "US-MP": "MP",  # Northern Mariana Islands
    "US-UM": "UM",  # United States Minor Outlying Islands
    "US-VI": "VI",  # US Virgin Islands
    # Netherlands — constituent countries within the Kingdom (Aruba, Curaçao;
    # Sint Maarten already comes through as NL-SX in Natural Earth's 3166-2
    # layer). US-PR / NL-SX are NOT in this map because they're already in
    # the 3166-2 subdivisions shipped by Natural Earth.
    "NL-AW": "AW",  # Aruba
    "NL-CW": "CW",  # Curaçao
}


# Where Natural Earth's English name disagrees with the ISO 3166-2 / Wikipedia
# convention, rewrite it here. Applied after all rows are gathered, just
# before write_labels.
LABEL_OVERRIDES: dict[str, str] = {
    "US-DC": "District of Columbia",  # NE ships "Washington" (the city)
}


# Tiny bounding polygon around Clipperton Island and its lagoon.
# Real extent is ~10°16'30" to 10°20' N, 109°10' to 109°15' W.
CLIPPERTON_FEATURE: dict = {
    "type": "Feature",
    "properties": {
        "iso_id": "FR-CP",
        "iso_name": "Clipperton",
        "name": "Clipperton",
        "source": "iso-3166-2-fr-synthetic",
    },
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [-109.250, 10.275],
            [-109.167, 10.275],
            [-109.167, 10.333],
            [-109.250, 10.333],
            [-109.250, 10.275],
        ]],
    },
}


# Current 13 metropolitan régions (since the 2016 reform), keyed by ISO 3166-2
# code → (English/French label, set of INSEE département numbers). Département
# numbers are stored as 2-character strings — "01" not "1", "2A"/"2B" for the
# two halves of Corsica.
CURRENT_REGIONS: dict[str, tuple[str, frozenset[str]]] = {
    "FR-ARA": ("Auvergne-Rhône-Alpes",
               frozenset({"01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74"})),
    "FR-BFC": ("Bourgogne-Franche-Comté",
               frozenset({"21", "25", "39", "58", "70", "71", "89", "90"})),
    "FR-BRE": ("Bretagne",
               frozenset({"22", "29", "35", "56"})),
    "FR-CVL": ("Centre-Val de Loire",
               frozenset({"18", "28", "36", "37", "41", "45"})),
    "FR-COR": ("Corse",
               frozenset({"2A", "2B"})),
    "FR-GES": ("Grand Est",
               frozenset({"08", "10", "51", "52", "54", "55", "57", "67", "68", "88"})),
    "FR-HDF": ("Hauts-de-France",
               frozenset({"02", "59", "60", "62", "80"})),
    "FR-IDF": ("Île-de-France",
               frozenset({"75", "77", "78", "91", "92", "93", "94", "95"})),
    "FR-NAQ": ("Nouvelle-Aquitaine",
               frozenset({"16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87"})),
    "FR-NOR": ("Normandie",
               frozenset({"14", "27", "50", "61", "76"})),
    "FR-OCC": ("Occitanie",
               frozenset({"09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"})),
    "FR-PDL": ("Pays de la Loire",
               frozenset({"44", "49", "53", "72", "85"})),
    "FR-PAC": ("Provence-Alpes-Côte d'Azur",
               frozenset({"04", "05", "06", "13", "83", "84"})),
}


# Pre-2016 22 metropolitan régions, suffixed in the label so consumers can tell
# the historical extent apart from the (often differently-shaped) successor.
_HIST_SUFFIX = " (historical region, pre-2016)"
HISTORIC_REGIONS: dict[str, tuple[str, frozenset[str]]] = {
    "FR-A": ("Alsace"             + _HIST_SUFFIX, frozenset({"67", "68"})),
    "FR-B": ("Aquitaine"          + _HIST_SUFFIX, frozenset({"24", "33", "40", "47", "64"})),
    "FR-C": ("Auvergne"           + _HIST_SUFFIX, frozenset({"03", "15", "43", "63"})),
    "FR-D": ("Bourgogne"          + _HIST_SUFFIX, frozenset({"21", "58", "71", "89"})),
    "FR-E": ("Bretagne"           + _HIST_SUFFIX, frozenset({"22", "29", "35", "56"})),
    "FR-F": ("Centre"             + _HIST_SUFFIX, frozenset({"18", "28", "36", "37", "41", "45"})),
    "FR-G": ("Champagne-Ardenne"  + _HIST_SUFFIX, frozenset({"08", "10", "51", "52"})),
    "FR-H": ("Corse"              + _HIST_SUFFIX, frozenset({"2A", "2B"})),
    "FR-I": ("Franche-Comté"      + _HIST_SUFFIX, frozenset({"25", "39", "70", "90"})),
    "FR-J": ("Île-de-France"      + _HIST_SUFFIX, frozenset({"75", "77", "78", "91", "92", "93", "94", "95"})),
    "FR-K": ("Languedoc-Roussillon" + _HIST_SUFFIX, frozenset({"11", "30", "34", "48", "66"})),
    "FR-L": ("Limousin"           + _HIST_SUFFIX, frozenset({"19", "23", "87"})),
    "FR-M": ("Lorraine"           + _HIST_SUFFIX, frozenset({"54", "55", "57", "88"})),
    "FR-N": ("Midi-Pyrénées"      + _HIST_SUFFIX, frozenset({"09", "12", "31", "32", "46", "65", "81", "82"})),
    "FR-O": ("Nord-Pas-de-Calais" + _HIST_SUFFIX, frozenset({"59", "62"})),
    "FR-P": ("Basse-Normandie"    + _HIST_SUFFIX, frozenset({"14", "50", "61"})),
    "FR-Q": ("Haute-Normandie"    + _HIST_SUFFIX, frozenset({"27", "76"})),
    "FR-R": ("Pays de la Loire"   + _HIST_SUFFIX, frozenset({"44", "49", "53", "72", "85"})),
    "FR-S": ("Picardie"           + _HIST_SUFFIX, frozenset({"02", "60", "80"})),
    "FR-T": ("Poitou-Charentes"   + _HIST_SUFFIX, frozenset({"16", "17", "79", "86"})),
    "FR-U": ("Provence-Alpes-Côte d'Azur" + _HIST_SUFFIX, frozenset({"04", "05", "06", "13", "83", "84"})),
    "FR-V": ("Rhône-Alpes"        + _HIST_SUFFIX, frozenset({"01", "07", "26", "38", "42", "69", "73", "74"})),
}


def _check() -> None:
    """Sanity-check the tables at import time would be heavy; only run manually."""
    # Each metropolitan département (96 of them: 01-95 + 2A + 2B, no 20) must
    # belong to exactly one current region and exactly one historic region.
    expected = {f"{n:02d}" for n in range(1, 96) if n != 20} | {"2A", "2B"}
    for label, table in [("current", CURRENT_REGIONS), ("historic", HISTORIC_REGIONS)]:
        seen: dict[str, str] = {}
        for region_id, (_, depts) in table.items():
            for d in depts:
                if d in seen:
                    raise AssertionError(f"{label}: département {d} is in both {seen[d]} and {region_id}")
                seen[d] = region_id
        missing = expected - set(seen)
        extra   = set(seen) - expected
        if missing or extra:
            raise AssertionError(f"{label}: missing={sorted(missing)} extra={sorted(extra)}")


if __name__ == "__main__":
    _check()
    print("fr_aliases.py: tables consistent")
