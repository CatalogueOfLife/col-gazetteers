"""Build the `iso` gazetteer — ISO 3166-1 alpha-2 countries + ISO 3166-2 subdivisions.

Source: Natural Earth 10m cultural shapefiles (admin_0_countries +
admin_1_states_provinces), the standard public-domain country / subdivision
geometry source.

Both kinds share one output namespace under `iso/features/`:
- 2-letter country codes:  `US.geojson`, `DE.geojson`, …
- 4–6-char subdivision codes: `US-CA.geojson`, `DE-BY.geojson`, …

All ids are stored upper-case (per ISO 3166). Features without a valid ISO
code (Natural Earth uses `-99` for non-ISO territories; subdivisions in
disputed / unrecognised areas often have an empty `iso_3166_2`) are skipped.

The backend's bundled vocabularies only cover ISO 3166-1 country codes — this
repo is the authoritative source for the 3166-2 subdivision labels.

A post-processing pass (see `_augment_french_codes` and `fr_aliases.py`) adds
French codes that Natural Earth doesn't ship as 3166-2: symlinks for the seven
overseas territories that are dual-coded (FR-PM → PM, etc.), a synthetic
polygon for Clipperton (FR-CP), and dissolved polygons for the 13 current
métropole régions plus the 22 pre-2016 historical régions.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import REPO_ROOT, SOURCES_DIR, WORK_DIR, load_config
from common.download import download
from common.ids import assert_unique
from common.labels import write_labels
from common.manifest import write_manifest
from common.ogr import split_features, to_geojson
from common.wikidata import sparql_csv
from iso.fr_aliases import (
    CLIPPERTON_FEATURE,
    CURRENT_REGIONS,
    HISTORIC_REGIONS,
    ISO_3166_3_DISSOLVES,
    LABEL_OVERRIDES,
    OVERSEAS_ALIASES,
    RESOLUTION_NOTES,
)
from iso.osm_augment import augment as augment_via_osm
from iso.wikidata_augment import augment as augment_via_wikidata_triage

PREFIX = "iso"
NE_BASE = "https://naciscdn.org/naturalearth/10m/cultural"

ID_FIELD = "iso_id"  # synthetic, written by the pre-processing pass below
NAME_FIELD = "iso_name"

# Subdivision codes from Natural Earth must match this pattern; anything
# else (empty string, partial code like "US-") is silently dropped.
_ISO_3166_2_RE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,3}$")
_ISO_3166_1_RE = re.compile(r"^[A-Z]{2}$")

# ISO 3166-1 user-assigned codes that Natural Earth ships as if they were
# real countries, but the ChecklistBank backend's country vocab does not
# accept. XK (Kosovo) and XZ (international waters) ARE in the backend's
# vocab, so they're not in this list.
_USER_ASSIGNED_NON_ISO = {"XD"}  # UN Disengagement Observer Force (Golan)


def _preprocess_countries(fc: dict) -> int:
    """Add iso_id (alpha-2) + iso_name (English) to each country feature; drop
    those without a valid ISO 3166-1 code. Returns the kept feature count.

    Natural Earth's `ISO_A2` is set to "-99" for politically contested or
    administratively-complex countries (notably France, Norway, Kosovo,
    Taiwan); the `ISO_A2_EH` ("estimated/historical") field carries the
    real alpha-2 code in those cases. We prefer ISO_A2_EH as the primary
    so those countries come through instead of being silently dropped.
    Natural Earth also uses non-standard values like "CN-TW" for Taiwan's
    ISO_A2; ISO_A2_EH normalises that to "TW".
    """
    kept = []
    for feat in fc["features"]:
        props = feat.get("properties") or {}
        raw = (props.get("ISO_A2_EH") or props.get("ISO_A2") or "").strip().upper()
        if raw == "-99" or not _ISO_3166_1_RE.match(raw):
            continue
        name = (props.get("NAME_EN")
                or props.get("NAME_LONG")
                or props.get("ADMIN")
                or props.get("NAME")
                or "").strip()
        props["iso_id"] = raw
        props["iso_name"] = name
        feat["properties"] = props
        kept.append(feat)
    fc["features"] = kept
    return len(kept)


def _preprocess_subunits(fc: dict, skip_codes: set[str]) -> int:
    """Promote NE map-subunit features keyed by ISO_A2_EH to top-level country
    polygons, but only for codes the admin_0 countries pass didn't already
    cover. This recovers ISO 3166-1 codes for overseas territories that NE's
    `ne_10m_admin_0_countries` rolls into the parent country (Svalbard SJ,
    Bouvet Island BV, French overseas departments GF/GP/MQ/RE, Christmas
    Island CX, Cocos CC, Caribbean Netherlands BQ, Tokelau TK, etc.)."""
    kept = []
    for feat in fc["features"]:
        props = feat.get("properties") or {}
        raw = (props.get("ISO_A2_EH") or props.get("ISO_A2") or "").strip().upper()
        if raw == "-99" or not _ISO_3166_1_RE.match(raw):
            continue
        if raw in skip_codes:
            continue
        if raw in _USER_ASSIGNED_NON_ISO:
            continue
        name = (props.get("NAME_EN")
                or props.get("NAME_LONG")
                or props.get("GEOUNIT")
                or props.get("SUBUNIT")
                or props.get("NAME")
                or "").strip()
        props["iso_id"] = raw
        props["iso_name"] = name
        feat["properties"] = props
        kept.append(feat)
    fc["features"] = kept
    return len(kept)


def _preprocess_subdivisions(fc: dict) -> int:
    """Add iso_id (CC-XX) + iso_name to each subdivision feature; drop those
    without a parseable ISO 3166-2 code."""
    kept = []
    for feat in fc["features"]:
        props = feat.get("properties") or {}
        raw = (props.get("iso_3166_2") or "").strip().upper()
        if not _ISO_3166_2_RE.match(raw):
            continue
        name = (props.get("name_en")
                or props.get("name")
                or props.get("gn_name")
                or "").strip()
        props["iso_id"] = raw
        props["iso_name"] = name
        feat["properties"] = props
        kept.append(feat)
    fc["features"] = kept
    return len(kept)


def _dissolve(
    subdivisions_fc: dict,
    member_to_group: dict[str, tuple[str, str]],
    *,
    features_dir: Path,
    work_dir: Path,
    source_tag: str,
) -> list[tuple[str, ...]]:
    """Relabel matching subdivision features with their group's ISO code and
    name, then run split_features so the merge-on-duplicate-id logic dissolves
    each group's members into one MultiPolygon.

    `member_to_group` maps a member's *full* iso_id (e.g. "FR-01", "GB-LBH",
    "ES-B") to (group_id, group_name) (e.g. ("FR-ARA", "Auvergne-Rhône-Alpes")).
    Members not present in `subdivisions_fc` are silently dropped — the
    dissolved polygon then represents only the subset that's actually built.
    """
    feats: list[dict] = []
    for feat in subdivisions_fc["features"]:
        iso_id = (feat.get("properties") or {}).get("iso_id", "")
        mapping = member_to_group.get(iso_id)
        if mapping is None:
            continue
        group_id, group_name = mapping
        feats.append({
            "type": "Feature",
            "properties": {
                **(feat.get("properties") or {}),
                "iso_id":   group_id,
                "iso_name": group_name,
            },
            "geometry": feat.get("geometry"),
        })
    relabel_path = work_dir / f"{source_tag}.geojson"
    with relabel_path.open("w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats},
                  f, ensure_ascii=False)
    return split_features(
        relabel_path, features_dir,
        id_field=ID_FIELD, name_field=NAME_FIELD,
        clear=False,
        source_tag=source_tag,
    )


def _dissolve_iso_3166_3(
    features_dir: Path,
    work_dir: Path,
) -> list[tuple[str, ...]]:
    """ISO 3166-3 historic country codes dissolved from their current
    successors. Reads each successor's country GeoJSON from features_dir,
    relabels with the historic id, and feeds the lot through split_features
    so duplicate-id merge produces one MultiPolygon per historic code.

    Returns the labels.tsv rows. Successors must already be written by the
    main pipeline (this runs after the country pass).
    """
    new_rows: list[tuple[str, ...]] = []
    for hist_id, (hist_name, successors) in ISO_3166_3_DISSOLVES.items():
        feats: list[dict] = []
        missing = []
        for s in successors:
            sp = features_dir / f"{s}.geojson"
            if not sp.exists() or sp.is_symlink():
                missing.append(s)
                continue
            with sp.open("r", encoding="utf-8") as f:
                src = json.load(f)
            feats.append({
                "type": "Feature",
                "properties": {ID_FIELD: hist_id, NAME_FIELD: hist_name},
                "geometry": src.get("geometry"),
            })
        if missing:
            print(f"[{PREFIX}] WARN: ISO 3166-3 {hist_id} successors missing: {missing}")
        if not feats:
            continue
        relabel_path = work_dir / f"iso-3166-3-{hist_id}.geojson"
        with relabel_path.open("w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": feats},
                      f, ensure_ascii=False)
        new_rows.extend(split_features(
            relabel_path, features_dir,
            id_field=ID_FIELD, name_field=NAME_FIELD,
            clear=False,
            source_tag="iso-3166-3-dissolved-successors",
        ))
    return new_rows


def _augment_french_codes(
    subdivisions_fc: dict | None,
    features_dir: Path,
    work_dir: Path,
    existing_rows: list[tuple[str, ...]],
) -> list[tuple[str, ...]]:
    """Add French codes that aren't in any Natural Earth layer.

    Three additions, in order:
      1. FR-PM/BL/MF/WF/PF/NC/TF: relative symlinks to the standalone
         ISO 3166-1 country geometry (PM.geojson etc.), with matching
         labels.tsv rows reusing the country's English name.
      2. FR-CP (Clipperton): synthetic bounding polygon.
      3. FR-ARA … FR-PAC and FR-A … FR-V: département polygons dissolved
         into current (post-2016) and historic (pre-2016) régions.

    Returns the new (id, name) rows to append to labels.tsv. The caller
    is responsible for `assert_unique` and write_labels.
    """
    if subdivisions_fc is None:
        raise RuntimeError("subdivisions FC was not captured — pipeline order changed?")

    new_rows: list[tuple[str, ...]] = []
    existing_by_id = {r[0]: r[1] for r in existing_rows}

    # 1. Overseas symlinks (only the FR-* entries from the shared table).
    new_rows.extend(_create_overseas_symlinks(
        features_dir, existing_by_id, prefix="FR-",
    ))

    # 2. Clipperton — synthetic geometry. Bypass split_features so the polygon
    # ships exactly as authored (no simplify pass; the box is already 5 points).
    cp_path = features_dir / "FR-CP.geojson"
    with cp_path.open("w", encoding="utf-8") as f:
        json.dump(CLIPPERTON_FEATURE, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    new_rows.append(("FR-CP", CLIPPERTON_FEATURE["properties"]["iso_name"]))

    # 3. Métropole régions — dissolve départements by region membership.
    for tag, region_map in [
        ("iso-3166-2-fr-region-current",  CURRENT_REGIONS),
        ("iso-3166-2-fr-region-historic", HISTORIC_REGIONS),
    ]:
        member_to_group = {
            f"FR-{dept}": (region_id, region_name)
            for region_id, (region_name, depts) in region_map.items()
            for dept in depts
        }
        new_rows.extend(_dissolve(
            subdivisions_fc, member_to_group,
            features_dir=features_dir, work_dir=work_dir, source_tag=tag,
        ))

    return new_rows


def _create_overseas_symlinks(
    features_dir: Path,
    existing_by_id: dict[str, str],
    *,
    prefix: str,
) -> list[tuple[str, ...]]:
    """Create CC-XX → XX.geojson relative symlinks for OVERSEAS_ALIASES entries
    whose alias id starts with `prefix` (e.g. "FR-", "US-", "NL-"). Returns
    labels.tsv rows reusing the standalone country's English name.
    """
    rows: list[tuple[str, ...]] = []
    for alias_id, target_id in OVERSEAS_ALIASES.items():
        if not alias_id.startswith(prefix):
            continue
        target_file = features_dir / f"{target_id}.geojson"
        if not target_file.exists():
            print(f"[{PREFIX}] WARN: alias {alias_id} target {target_id}.geojson "
                  f"not present in build — skipping")
            continue
        link = features_dir / f"{alias_id}.geojson"
        # `os.symlink(src, dst)` resolves src relative to dst's directory, so
        # a bare filename is the right relative form for siblings.
        os.symlink(f"{target_id}.geojson", link)
        rows.append((alias_id, existing_by_id.get(target_id, target_id)))
    return rows


# SPARQL queries used by `_augment_via_wikidata`. Each returns a CSV with the
# columns documented in its leading comment. The queries are kept here (rather
# than in fr_aliases.py) because they're not opinion — just authoritative
# lookups that the build needs to dissolve subdivisions into their parent.

_WIKIDATA_GB_HOME_NATIONS_QUERY = """\
# subCode (e.g. "GB-LBH"), nationCode (always GB-ENG/GB-SCT/GB-WLS/GB-NIR),
# nationLabel (English name of the home nation).
SELECT DISTINCT ?subCode ?nationCode ?nationLabel WHERE {
  ?sub wdt:P300 ?subCode .
  FILTER(STRSTARTS(?subCode, "GB-") && STRLEN(?subCode) <= 6)
  ?sub wdt:P131* ?nation .
  VALUES (?nation ?nationCode ?nationLabel) {
    (wd:Q21 "GB-ENG" "England")
    (wd:Q22 "GB-SCT" "Scotland")
    (wd:Q25 "GB-WLS" "Wales")
    (wd:Q26 "GB-NIR" "Northern Ireland")
  }
}
"""

_WIKIDATA_ES_AC_QUERY = """\
# provCode (e.g. "ES-B"), provLabel, acCode (autonomous-community id, e.g. "ES-CT"),
# acLabel (English name of the autonomous community).
SELECT DISTINCT ?provCode ?provLabel ?acCode ?acLabel WHERE {
  ?prov wdt:P300 ?provCode .
  FILTER(STRSTARTS(?provCode, "ES-") && STRLEN(?provCode) <= 5)
  ?prov wdt:P131 ?ac .
  ?ac wdt:P300 ?acCode .
  FILTER(STRSTARTS(?acCode, "ES-") && STRLEN(?acCode) <= 5)
  FILTER(?acCode != ?provCode)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""

# Wikidata oddities to paper over: ES-LO (La Rioja province) is a current
# ISO 3166-2 code but Wikidata only registers the autonomous-community-level
# ES-RI on that entity, so the SPARQL join above misses it. La Rioja the
# autonomous community has exactly one province (also La Rioja), so the
# mapping is unambiguous.
_ES_MANUAL_PROVINCE_TO_AC: dict[str, tuple[str, str]] = {
    "ES-LO": ("ES-RI", "La Rioja"),
}


def _augment_via_wikidata(
    subdivisions_fc: dict,
    features_dir: Path,
    work_dir: Path,
    existing_rows: list[tuple[str, ...]],
    *,
    force: bool,
) -> tuple[list[tuple[str, ...]], list]:
    """Run the SPARQL-driven additions: GB home-nation dissolves and ES
    autonomous-community dissolves. Returns (new rows, source records).
    """
    new_rows: list[tuple[str, ...]] = []
    existing_by_id = {r[0]: r[1] for r in existing_rows}
    existing_ids = set(existing_by_id)
    source_records = []

    # --- GB home nations (ENG/SCT/WLS/NIR) ---
    gb_csv_path, gb_record = sparql_csv(
        _WIKIDATA_GB_HOME_NATIONS_QUERY,
        SOURCES_DIR / PREFIX / "wikidata_gb_home_nations.csv",
        role="iso-3166-2-gb-home-nations",
        name="Wikidata SPARQL: GB sub-divisions → home nation",
        force=force,
    )
    source_records.append(gb_record)

    gb_members: dict[str, tuple[str, str]] = {}
    gb_group_labels: dict[str, str] = {}
    with gb_csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sub, nation, nation_label = row["subCode"], row["nationCode"], row["nationLabel"]
            if sub in existing_ids and sub not in gb_members:
                gb_members[sub] = (nation, nation_label)
                gb_group_labels[nation] = nation_label

    print(f"[{PREFIX}] Wikidata GB: {len(gb_members)}/{sum(1 for c in existing_ids if c.startswith('GB-'))} "
          f"sub-divisions mapped to a home nation")
    new_rows.extend(_dissolve(
        subdivisions_fc, gb_members,
        features_dir=features_dir, work_dir=work_dir,
        source_tag="iso-3166-2-gb-home-nation",
    ))

    # --- ES autonomous communities ---
    es_csv_path, es_record = sparql_csv(
        _WIKIDATA_ES_AC_QUERY,
        SOURCES_DIR / PREFIX / "wikidata_es_autonomous_communities.csv",
        role="iso-3166-2-es-autonomous-communities",
        name="Wikidata SPARQL: ES provinces → autonomous community",
        force=force,
    )
    source_records.append(es_record)

    es_members: dict[str, tuple[str, str]] = {}
    with es_csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            prov, ac, ac_label = row["provCode"], row["acCode"], row["acLabel"]
            if prov in existing_ids and prov not in es_members:
                es_members[prov] = (ac, ac_label)
    # Apply manual additions for Wikidata gaps (see _ES_MANUAL_PROVINCE_TO_AC).
    for prov, (ac, ac_label) in _ES_MANUAL_PROVINCE_TO_AC.items():
        if prov in existing_ids:
            es_members.setdefault(prov, (ac, ac_label))

    print(f"[{PREFIX}] Wikidata ES: {len(es_members)} provinces mapped to "
          f"autonomous communities (+{len(_ES_MANUAL_PROVINCE_TO_AC)} manual)")
    new_rows.extend(_dissolve(
        subdivisions_fc, es_members,
        features_dir=features_dir, work_dir=work_dir,
        source_tag="iso-3166-2-es-autonomous-community",
    ))

    return new_rows, source_records


# Source-tag → high-level resolution mode. Source tags written by the build
# pipeline (via `properties.source` on each feature) carry more detail than
# we want in the user-facing sources.tsv; this map collapses them into a few
# coarse categories.
_RESOLUTION_BY_SOURCE: dict[str, str] = {
    "iso-3166-1-countries":                          "upstream",
    "iso-3166-1-subunits":                           "upstream",
    "iso-3166-2-subdivisions":                       "upstream",
    "iso-3166-2-fr-region-current":                  "dissolved",
    "iso-3166-2-fr-region-historic":                 "dissolved",
    "iso-3166-2-gb-home-nation":                     "dissolved",
    "iso-3166-2-es-autonomous-community":            "dissolved",
    "iso-3166-2-fr-synthetic":                       "synthetic",
    "iso-3166-2-placeholder-circle":                 "placeholder-circle",
    "iso-3166-2-placeholder-country-centroid":       "placeholder-country-centroid",
    "iso-3166-3-dissolved-successors":               "dissolved",
}


def _write_sources_tsv(
    path: Path,
    all_rows: list[tuple[str, ...]],
    features_dir: Path,
    label_overrides: dict[str, str],
    explicit_source_rows: dict[str, tuple[str, str, str, str]] | None = None,
    notes: dict[str, str] | None = None,
) -> None:
    """Write a per-id provenance table next to labels.tsv.

    Columns: id, resolution, upstream, target, note.

    Resolution is one of {upstream, upstream-relabel, alias-symlink,
    alias-symlink-superseded, alias-symlink-withdrawn, dissolved,
    synthetic, placeholder-circle, placeholder-country-centroid}.
    `upstream` is the raw source tag stamped on the feature at build time
    (or, for symlinks, the target's source tag). `target` is only set for
    alias-symlinks. The backend ignores this file — it's for humans,
    debugging, and downstream tooling that wants to know how a given id
    was put together.

    `explicit_source_rows` is an optional map of id → (resolution, upstream,
    target, note) that overrides the auto-classification — used by the
    Wikidata triage pass to record nuance the on-disk file can't carry
    (e.g. that a particular symlink is a superseded code, not just an alias).
    """
    explicit = explicit_source_rows or {}
    extra_notes = notes or {}
    rows: list[tuple[str, str, str, str, str]] = []
    for row in sorted(all_rows, key=lambda r: r[0]):
        aid = row[0]
        if aid in explicit:
            resolution, upstream, target, note = explicit[aid]
            if not note and aid in extra_notes:
                note = extra_notes[aid]
            rows.append((aid, resolution, upstream, target, note))
            continue
        feat_path = features_dir / f"{aid}.geojson"
        if feat_path.is_symlink():
            target_name = os.readlink(feat_path).removesuffix(".geojson")
            target_path = features_dir / f"{target_name}.geojson"
            upstream = _read_source(target_path)
            note = extra_notes.get(aid, "")
            rows.append((aid, "alias-symlink", upstream, target_name, note))
            continue
        upstream = _read_source(feat_path)
        if aid in label_overrides:
            resolution = "upstream-relabel"
            note = f'label override: "{label_overrides[aid]}"'
        else:
            resolution = _RESOLUTION_BY_SOURCE.get(upstream, "upstream")
            note = extra_notes.get(aid, "")
        rows.append((aid, resolution, upstream, "", note))

    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("id\tresolution\tupstream\ttarget\tnote\n")
        for r in rows:
            f.write("\t".join(r) + "\n")


def _read_source(feat_path: Path) -> str:
    """Return `properties.source` from a feature file, or '' if missing."""
    try:
        with feat_path.open("r", encoding="utf-8") as f:
            feat = json.load(f)
    except FileNotFoundError:
        return ""
    return (feat.get("properties") or {}).get("source", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crs", choices=["4326", "3857"], default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = load_config(cli_crs=args.crs)
    out_dir = REPO_ROOT / PREFIX
    features_dir = out_dir / "features"
    work_dir = WORK_DIR / PREFIX
    work_dir.mkdir(parents=True, exist_ok=True)
    features_dir.mkdir(parents=True, exist_ok=True)
    for old in features_dir.glob("*.geojson"):
        old.unlink()

    print(f"[{PREFIX}] target CRS: {config.epsg}, simplify {config.simplify_tolerance}")

    sources = []
    all_rows: list[tuple[str, ...]] = []
    country_codes_seen: set[str] = set()
    subdivisions_fc: dict | None = None  # captured for the FR-region dissolve pass

    # The subunit pass needs to know which country codes the admin_0 pass
    # already covered, so we drive the three sources explicitly rather than
    # generically.
    sources_to_process = [
        ("ne_10m_admin_0_countries",        "iso-3166-1-countries",
         lambda fc: _preprocess_countries(fc)),
        ("ne_10m_admin_0_map_subunits",     "iso-3166-1-subunits",
         lambda fc: _preprocess_subunits(fc, country_codes_seen)),
        ("ne_10m_admin_1_states_provinces", "iso-3166-2-subdivisions",
         lambda fc: _preprocess_subdivisions(fc)),
    ]

    for name, role, level_preprocess in sources_to_process:
        url = f"{NE_BASE}/{name}.zip"
        zip_path, record = download(
            url, SOURCES_DIR / PREFIX,
            role=role, name=f"Natural Earth 10m: {name}",
            filename=f"{name}.zip", force=args.force,
            upstream_version="10m",
        )
        sources.append(record)
        print(f"[{PREFIX}] {name}: {record.size_bytes:,} bytes md5={record.md5[:8]}…")

        vsizip_src = f"/vsizip/{zip_path}/{name}.shp"
        raw_fc = work_dir / f"{name}.geojson"
        to_geojson(vsizip_src, raw_fc, config)

        with raw_fc.open("r", encoding="utf-8") as f:
            fc = json.load(f)
        before = len(fc["features"])
        kept = level_preprocess(fc)
        rewritten_fc = work_dir / f"{name}.iso.geojson"
        with rewritten_fc.open("w", encoding="utf-8") as f:
            json.dump(fc, f, ensure_ascii=False)
        print(f"[{PREFIX}] {name}: kept {kept}/{before} features with valid ISO codes")

        rows = split_features(
            rewritten_fc, features_dir,
            id_field=ID_FIELD, name_field=NAME_FIELD,
            clear=False,
            source_tag=role,
        )
        all_rows.extend(rows)
        if role == "iso-3166-1-countries":
            country_codes_seen.update(r[0] for r in rows)
        if role == "iso-3166-2-subdivisions":
            subdivisions_fc = fc

    fr_rows = _augment_french_codes(subdivisions_fc, features_dir, work_dir, all_rows)
    all_rows.extend(fr_rows)
    print(f"[{PREFIX}] FR augmentation: +{len(fr_rows)} ids "
          f"(overseas aliases, FR-CP, current + historic métropole régions)")

    # Non-FR overseas symlinks (US-AS/GU/MP/UM/VI, NL-AW/CW) and CLB-legacy
    # codes (TP, BQ-SA, BQ-BO) that aren't FR/US/NL prefixed.
    existing_by_id = {r[0]: r[1] for r in all_rows}
    for non_fr_prefix in ("US-", "NL-", "TP", "BQ-"):
        extra = _create_overseas_symlinks(features_dir, existing_by_id, prefix=non_fr_prefix)
        all_rows.extend(extra)
        if extra:
            print(f"[{PREFIX}] {non_fr_prefix!r} symlinks: +{len(extra)} ids")

    # ISO 3166-3 historic country codes dissolved from current successors.
    iso3_rows = _dissolve_iso_3166_3(features_dir, work_dir)
    all_rows.extend(iso3_rows)
    if iso3_rows:
        print(f"[{PREFIX}] ISO 3166-3 dissolves: +{len(iso3_rows)} ids "
              f"({', '.join(r[0] for r in iso3_rows)})")

    assert subdivisions_fc is not None  # narrowed by the loop's invariant
    wd_rows, wd_sources = _augment_via_wikidata(
        subdivisions_fc, features_dir, work_dir, all_rows, force=args.force,
    )
    all_rows.extend(wd_rows)
    sources.extend(wd_sources)
    print(f"[{PREFIX}] Wikidata augmentation: +{len(wd_rows)} ids "
          f"(GB home nations + ES autonomous communities)")

    # Apply curated label rewrites (e.g. NE's "Washington" → ISO "District of
    # Columbia"). Only touches rows whose id is in the override table.
    if LABEL_OVERRIDES:
        rewritten = 0
        for i, row in enumerate(all_rows):
            new_label = LABEL_OVERRIDES.get(row[0])
            if new_label is not None and row[1] != new_label:
                all_rows[i] = (row[0], new_label, *row[2:])
                rewritten += 1
        if rewritten:
            print(f"[{PREFIX}] label overrides applied: {rewritten}")

    # Phase 2: every ISO 3166-2 code Wikidata knows about but we don't ship.
    # Adds aliases for true dual-codes and superseded codes, placeholder
    # circles for the rest.
    existing_ids = {r[0] for r in all_rows}
    wd_labels, wd_source_rows, wd_records = augment_via_wikidata_triage(
        features_dir, SOURCES_DIR / PREFIX, existing_ids, force=args.force,
    )
    all_rows.extend(wd_labels)
    sources.extend(wd_records)
    explicit_sources = {r[0]: (r[1], r[2], r[3], r[4]) for r in wd_source_rows}
    print(f"[{PREFIX}] Wikidata triage augmentation: +{len(wd_labels)} ids")

    # Phase 3: replace placeholder circles with real OSM polygons for any
    # country that has ≥5 placeholders. Writes a temporary intermediate
    # sources.tsv so osm_augment can read the placeholder list it just
    # produced; the final sources.tsv is rewritten below with the OSM
    # replacements merged in.
    _write_sources_tsv(
        out_dir / "sources.tsv", all_rows, features_dir, LABEL_OVERRIDES,
        explicit_source_rows=explicit_sources,
        notes=RESOLUTION_NOTES,
    )
    osm_replacements, osm_records = augment_via_osm(
        features_dir, SOURCES_DIR / PREFIX, out_dir / "sources.tsv",
        existing_labels={r[0]: r[1] for r in all_rows},
        simplify_tolerance=config.simplify_tolerance,
        min_country_count=5,
        force=args.force,
    )
    sources.extend(osm_records)
    # Merge OSM-replacement rows into the explicit-source-rows map so the
    # final sources.tsv records `upstream` (not `placeholder-circle`) for
    # each replaced id.
    explicit_sources.update(osm_replacements)
    print(f"[{PREFIX}] OSM augmentation: {len(osm_replacements)} placeholders → real polygons")

    assert_unique([r[0] for r in all_rows])
    label_count = write_labels(out_dir / "labels.tsv", all_rows)
    _write_sources_tsv(
        out_dir / "sources.tsv", all_rows, features_dir, LABEL_OVERRIDES,
        explicit_source_rows=explicit_sources,
        notes=RESOLUTION_NOTES,
    )
    feature_count = len(list(features_dir.glob("*.geojson")))
    print(f"[{PREFIX}] total: {feature_count} features, {label_count} labels, "
          f"sources.tsv written")

    write_manifest(
        out_dir / "build.json",
        prefix=PREFIX,
        config=config,
        sources=sources,
        feature_count=feature_count,
        label_count=label_count,
    )
    print(f"[{PREFIX}] manifest → {out_dir / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
