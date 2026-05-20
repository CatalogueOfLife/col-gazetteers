"""Phase 3 augmentation: replace placeholder circles with real OSM polygons.

After the Wikidata triage pass leaves us with ~1,200 placeholder circles
for ISO 3166-2 codes Natural Earth doesn't ship, this pass walks every
country with ≥5 placeholders and asks OpenStreetMap (via Overpass) for
admin relations tagged `ISO3166-2=CC-XX`. Where OSM has a relation with
the matching tag, the placeholder is replaced by the OSM MultiPolygon
(simplified to the build's tolerance).

OSM tagging coverage varies by country — when an ISO code has no OSM
match the placeholder stays. The per-id resolution mode in sources.tsv
flips from `placeholder-circle` to `upstream` (with source tag
`osm-overpass-<cc>`) for each replacement.

Licensing: OSM data is ODbL. Caller's `build.json` records the Overpass
endpoint + query SHA so the provenance trail is reproducible.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import osm2geojson
from shapely.geometry import mapping, shape

from common.download import SourceRecord
from common.overpass import fetch as overpass_fetch


# Per-country Overpass query: every admin relation inside CC that has an
# ISO3166-2 tag, with full member geometry inlined.
def _overpass_query(cc: str) -> str:
    return f"""\
[out:json][timeout:300];
area["ISO3166-1"="{cc}"][admin_level=2]->.country;
(
  relation["boundary"="administrative"]["ISO3166-2"~"^{cc}-"](area.country);
);
out geom;
"""


def _placeholders_by_cc(sources_tsv: Path, min_count: int) -> dict[str, set[str]]:
    """Return {country_cc: {id, ...}} for placeholder rows, only countries
    whose placeholder count ≥ min_count."""
    out: dict[str, set[str]] = defaultdict(set)
    with sources_tsv.open("r", encoding="utf-8") as f:
        next(f)  # header
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or "placeholder" not in parts[1]:
                continue
            code = parts[0]
            if "-" not in code:
                continue
            out[code.split("-", 1)[0]].add(code)
    return {cc: codes for cc, codes in out.items() if len(codes) >= min_count}


def _write_feature(
    features_dir: Path,
    code: str,
    label: str,
    geom: dict,
    source_tag: str,
) -> None:
    feat = {
        "type": "Feature",
        "properties": {
            "iso_id":   code,
            "iso_name": label,
            "name":     label,
            "source":   source_tag,
        },
        "geometry": geom,
    }
    with (features_dir / f"{code}.geojson").open("w", encoding="utf-8") as f:
        json.dump(feat, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def augment(
    features_dir: Path,
    sources_dir: Path,
    sources_tsv: Path,
    existing_labels: dict[str, str],
    *,
    simplify_tolerance: float,
    min_country_count: int,
    force: bool,
) -> tuple[dict[str, tuple[str, str, str, str]], list[SourceRecord]]:
    """For each country with ≥ min_country_count placeholders, fetch OSM
    admin relations and replace matching placeholders with real polygons.

    Returns:
      replaced_source_rows  — id → (resolution, upstream, target, note) for
                               sources.tsv to override the old placeholder row.
      source_records        — one SourceRecord per country's cached Overpass response.
    """
    targets = _placeholders_by_cc(sources_tsv, min_country_count)
    print(f"[iso] OSM augmentation: {len(targets)} countries with ≥{min_country_count} "
          f"placeholders ({sum(len(c) for c in targets.values())} candidate ids)")

    replaced_source_rows: dict[str, tuple[str, str, str, str]] = {}
    source_records: list[SourceRecord] = []
    countries_done = 0
    failed_cc: list[str] = []

    for cc in sorted(targets):
        codes = targets[cc]
        cache_path = sources_dir / f"osm_{cc}_admin.json"
        try:
            cache_path, record = overpass_fetch(
                _overpass_query(cc), cache_path,
                role=f"osm-overpass-{cc.lower()}",
                name=f"OSM Overpass: {cc} admin relations with ISO3166-2 tag",
                force=force,
            )
        except RuntimeError as e:
            print(f"  [{cc}] Overpass fetch failed — keeping placeholders: {e}")
            failed_cc.append(cc)
            # Be polite to the next endpoint after a string of failures.
            time.sleep(10)
            continue
        source_records.append(record)

        with cache_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        fc = osm2geojson.json2geojson(raw)

        replaced = 0
        for feat in fc.get("features", []):
            props = feat.get("properties") or {}
            tags = props.get("tags") or {}
            code = tags.get("ISO3166-2", "")
            if code not in codes:
                continue
            geom = feat.get("geometry")
            if not geom:
                continue
            # Simplify to the build's tolerance using shapely.
            simplified = shape(geom).simplify(simplify_tolerance, preserve_topology=True)
            if simplified.is_empty:
                continue
            label = existing_labels.get(code, code)
            source_tag = f"osm-overpass-{cc.lower()}"
            _write_feature(features_dir, code, label,
                           _round_coords(mapping(simplified), 6), source_tag)
            replaced_source_rows[code] = (
                "upstream", source_tag, "",
                f"OSM relation tagged ISO3166-2={code}",
            )
            replaced += 1
        countries_done += 1
        print(f"  [{cc}] {replaced}/{len(codes)} placeholders replaced "
              f"({100*replaced/len(codes):.0f}%)")
        # Light politeness delay between countries.
        time.sleep(1)

    print(f"[iso] OSM augmentation: {countries_done}/{len(targets)} countries processed, "
          f"{len(replaced_source_rows)} placeholders replaced "
          f"({len(failed_cc)} countries failed: {failed_cc})")
    return replaced_source_rows, source_records


def _round_coords(geom: dict, decimals: int) -> dict:
    """Round all numeric coordinates in a GeoJSON geometry. Saves disk &
    keeps diffs stable."""
    def r(v):
        if isinstance(v, (int, float)):
            return round(v, decimals)
        if isinstance(v, list):
            return [r(x) for x in v]
        return v
    out = dict(geom)
    out["coordinates"] = r(geom["coordinates"])
    return out
