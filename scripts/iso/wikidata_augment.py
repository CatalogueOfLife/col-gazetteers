"""Phase 2 augmentation: add every ISO 3166-2 code Wikidata knows about that
we don't already ship.

Triage classifies each missing code into one of six categories; each gets a
different treatment:

  A — dual-coded: the sub-code refers to the same place as an ISO 3166-1
      code we already ship. Tiny curated list (FI-01 → AX, NO-22 → SJ,
      GB-JSY → JE, DK-GL → GL). Implementation: relative symlink to the
      standalone country file, same mechanism as `OVERSEAS_ALIASES`.
  B — child of a shipped subdivision: parent code is in our labels.tsv
      and Wikidata gives us a coordinate location (P625). Implementation:
      32-sided 0.5°-radius placeholder polygon at those coordinates.
  C — superseded with a successor we ship: replaced-by (P1366) points to
      one or more codes in our labels.tsv. 1:1 → symlink to successor.
      1:many → placeholder at coords (the old extent is a union of the
      successors, no single successor shape matches).
  D — withdrawn (P582 end-date), no usable successor. Placeholder at
      coords if available, else country centroid.
  E — has coords, no parent we ship, not withdrawn. Placeholder at coords.
  F — none of the above (no parent, no coords, no successor). Falls back
      to the parent country's centroid. If the country isn't ours either,
      we drop the code entirely.

Placeholder polygons are tagged `placeholder-circle-coord` (or
`-country-centroid`) in `properties.source`, and `sources.tsv` records
which category each id came from. Consumers can filter on either.
"""

from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from common.download import SourceRecord
from common.wikidata import sparql_csv


# Curated additional dual-codes beyond what fr_aliases.OVERSEAS_ALIASES
# covers. Each entry must point at a 3166-1 code that's already in our build.
KNOWN_DUAL: dict[str, str] = {
    "FI-01":  "AX",  # Åland Islands
    "NO-22":  "SJ",  # Jan Mayen (SJ also covers Svalbard)
    "GB-JSY": "JE",  # Jersey (withdrawn from 3166-2 in 2007; Wikidata keeps it)
    "GB-GSY": "GG",  # Guernsey (withdrawn 2007)
    "GB-IOM": "IM",  # Isle of Man (withdrawn 2007)
    "DK-GL":  "GL",  # Greenland (when Wikidata still ships)
    "DK-FO":  "FO",  # Faroe Islands (when Wikidata still ships)
    # NO-21 Svalbard is already shipped by Natural Earth as a real polygon.
}


PLACEHOLDER_RADIUS_DEG = 0.5  # ~55 km at the equator
PLACEHOLDER_VERTICES = 32


_TRIAGE_QUERY = """\
# Every ISO 3166-2 code Wikidata knows, with metadata used by the triage:
# - replacedByCode: P1366 → P300, lets us symlink superseded codes to their
#   successor when 1:1, or detect splits when 1:many.
# - parentCode:     P131 → P300, "located in administrative entity".
# - lat/lon:        P625 coordinate location, projected by geof.
# - endDate:        P582, set when a code was withdrawn from the standard.
# - label:          English label for labels.tsv.
SELECT ?code ?label ?endDate ?replacedByCode ?parentCode ?lat ?lon WHERE {
  ?e wdt:P300 ?code .
  FILTER(REGEX(?code, "^[A-Z]{2}-[A-Z0-9]{1,3}$"))
  OPTIONAL { ?e wdt:P1366 ?rb . ?rb wdt:P300 ?replacedByCode }
  OPTIONAL { ?e wdt:P131 ?p . ?p wdt:P300 ?parentCode }
  OPTIONAL {
    ?e wdt:P625 ?coord .
    BIND(geof:longitude(?coord) AS ?lon)
    BIND(geof:latitude(?coord)  AS ?lat)
  }
  OPTIONAL { ?e wdt:P582 ?endDate }
  OPTIONAL { ?e rdfs:label ?label . FILTER(LANG(?label) = "en") }
}
"""


@dataclass
class _Meta:
    label: str = ""
    parents: set[str] = field(default_factory=set)
    replaced_by: set[str] = field(default_factory=set)
    end_dates: set[str] = field(default_factory=set)
    coords: tuple[float, float] | None = None  # (lat, lon)


def _load_triage_csv(path: Path) -> dict[str, _Meta]:
    """Read the triage CSV (possibly with multiple rows per code) into a
    code → _Meta dict."""
    out: dict[str, _Meta] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            code = row.get("code") or ""
            if not code:
                continue
            m = out.setdefault(code, _Meta())
            if row.get("label") and not m.label:
                m.label = row["label"]
            if row.get("parentCode"):
                m.parents.add(row["parentCode"])
            if row.get("replacedByCode"):
                m.replaced_by.add(row["replacedByCode"])
            if row.get("endDate"):
                m.end_dates.add(row["endDate"])
            if row.get("lat") and row.get("lon") and m.coords is None:
                m.coords = (float(row["lat"]), float(row["lon"]))
    return out


def _make_circle_polygon(lon: float, lat: float,
                         radius: float = PLACEHOLDER_RADIUS_DEG,
                         vertices: int = PLACEHOLDER_VERTICES) -> dict:
    """Return a GeoJSON Polygon approximating a circle in (lon, lat) degree
    space. Not a true geographic circle — at high latitudes the east-west
    extent in metres shrinks vs the north-south one — but that's fine for a
    visible "approximately here" placeholder."""
    coords = []
    for i in range(vertices):
        angle = 2 * math.pi * i / vertices
        coords.append([
            round(lon + radius * math.cos(angle), 6),
            round(lat + radius * math.sin(angle), 6),
        ])
    coords.append(coords[0])  # close the ring (RFC 7946 §3.1.6)
    return {"type": "Polygon", "coordinates": [coords]}


def _polygon_centroid(geom: dict) -> tuple[float, float] | None:
    """Compute the area-weighted centroid (lon, lat) of a Polygon or
    MultiPolygon. For MultiPolygons, picks the largest ring by signed
    area. Returns None for geometries we can't handle."""
    if geom is None:
        return None
    t = geom.get("type")
    if t == "Polygon":
        rings = [geom["coordinates"][0]]
    elif t == "MultiPolygon":
        rings = [poly[0] for poly in geom["coordinates"]]
    else:
        return None
    if not rings:
        return None
    # Pick the ring with the largest |signed area|.
    best_ring, best_area = None, 0.0
    for ring in rings:
        a = 0.0
        for i in range(len(ring) - 1):
            x0, y0 = ring[i]
            x1, y1 = ring[i + 1]
            a += x0 * y1 - x1 * y0
        if abs(a) > abs(best_area):
            best_area = a
            best_ring = ring
    if best_ring is None or best_area == 0:
        return None
    # Centroid of the chosen ring via the standard polygon-centroid formula.
    cx = cy = 0.0
    for i in range(len(best_ring) - 1):
        x0, y0 = best_ring[i]
        x1, y1 = best_ring[i + 1]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    cx /= (3 * best_area)
    cy /= (3 * best_area)
    return (cx, cy)


def _write_placeholder(
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


def _country_centroid_cache(features_dir: Path) -> dict[str, tuple[float, float]]:
    """Lazy-load country polygons and compute their centroids once."""
    out: dict[str, tuple[float, float]] = {}
    for path in features_dir.glob("??.geojson"):
        # Country-level: filename is exactly 2 chars + .geojson, no symlinks.
        if path.is_symlink():
            continue
        with path.open("r", encoding="utf-8") as f:
            feat = json.load(f)
        c = _polygon_centroid(feat.get("geometry"))
        if c is not None:
            out[path.stem] = c
    return out


def augment(
    features_dir: Path,
    sources_dir: Path,
    existing_ids: set[str],
    *,
    force: bool,
) -> tuple[list[tuple[str, ...]], list[tuple[str, str, str, str, str]], list[SourceRecord]]:
    """Run the triage + per-category materialisation.

    Returns:
      labels_rows  — (id, label) tuples to extend labels.tsv with
      source_rows  — (id, resolution, upstream, target, note) tuples for
                     sources.tsv; the caller will merge these with the
                     rows it already wrote for upstream features.
      sources     — SourceRecords for the Wikidata triage query, for
                     build.json provenance.
    """
    cache_path = sources_dir / "wikidata_iso_3166_2_triage.csv"
    cache_path, record = sparql_csv(
        _TRIAGE_QUERY, cache_path,
        role="iso-3166-2-wikidata-triage",
        name="Wikidata SPARQL: all ISO 3166-2 codes with metadata for triage",
        force=force,
    )
    triage = _load_triage_csv(cache_path)

    missing = {c: m for c, m in triage.items() if c not in existing_ids}
    print(f"[iso] Wikidata triage: {len(triage)} codes in Wikidata, "
          f"{len(missing)} not in our build yet")

    ours_country = {c for c in existing_ids if "-" not in c and len(c) == 2}
    centroids = _country_centroid_cache(features_dir)

    labels_rows: list[tuple[str, ...]] = []
    source_rows: list[tuple[str, str, str, str, str]] = []
    cat_counts: dict[str, int] = defaultdict(int)
    dropped: list[str] = []

    for code in sorted(missing):
        m = missing[code]
        label = m.label or code
        cc = code.split("-", 1)[0]

        # A — known dual-coded.
        target = KNOWN_DUAL.get(code)
        if target and target in ours_country:
            _create_symlink(features_dir, code, target)
            labels_rows.append((code, label))
            source_rows.append((code, "alias-symlink", "iso-3166-1-countries",
                                target, "dual-coded with ISO 3166-1"))
            cat_counts["A-dual-coded-known"] += 1
            continue

        # C — superseded.
        successors_in_ours = sorted(rb for rb in (m.replaced_by or ()) if rb in existing_ids)
        if successors_in_ours and not m.end_dates and len(successors_in_ours) == 1:
            target = successors_in_ours[0]
            _create_symlink(features_dir, code, target)
            labels_rows.append((code, label))
            source_rows.append((code, "alias-symlink-superseded",
                                "wikidata-p1366", target,
                                "superseded by current code"))
            cat_counts["C-superseded-1to1"] += 1
            continue

        # D — withdrawn with successor we ship (1:1 only).
        if m.end_dates and len(successors_in_ours) == 1:
            target = successors_in_ours[0]
            _create_symlink(features_dir, code, target)
            labels_rows.append((code, label))
            source_rows.append((code, "alias-symlink-withdrawn",
                                "wikidata-p1366", target,
                                "withdrawn from ISO 3166-2"))
            cat_counts["D-withdrawn-successor-1to1"] += 1
            continue

        # B/C-1:many/D-no-successor/E — placeholder circle at coords.
        if m.coords is not None:
            lat, lon = m.coords
            geom = _make_circle_polygon(lon, lat)
            _write_placeholder(features_dir, code, label, geom,
                               "iso-3166-2-placeholder-circle")
            labels_rows.append((code, label))
            note_parts = []
            if m.parents:
                parents_in_ours = sorted(p for p in m.parents if p in existing_ids)
                if parents_in_ours:
                    note_parts.append(f"parent={','.join(parents_in_ours)}")
            if successors_in_ours and len(successors_in_ours) > 1:
                note_parts.append(f"split-into={','.join(successors_in_ours)}")
            if m.end_dates:
                note_parts.append("withdrawn")
            source_rows.append((code, "placeholder-circle",
                                "iso-3166-2-placeholder-circle", "",
                                "; ".join(note_parts) or
                                f"coords from Wikidata P625"))
            cat_counts["BCDE-placeholder-circle-at-coords"] += 1
            continue

        # F — no coords. Try the country centroid.
        if cc in centroids:
            lon_lat = centroids[cc]
            geom = _make_circle_polygon(lon_lat[0], lon_lat[1])
            _write_placeholder(features_dir, code, label, geom,
                               "iso-3166-2-placeholder-country-centroid")
            labels_rows.append((code, label))
            source_rows.append((code, "placeholder-country-centroid",
                                "iso-3166-2-placeholder-country-centroid", "",
                                f"no coords in Wikidata; centroid of {cc}"))
            cat_counts["F-country-centroid"] += 1
            continue

        # No country, no coords, no successor — drop.
        dropped.append(code)

    print("[iso] Wikidata triage results:")
    for cat in sorted(cat_counts):
        print(f"  {cat:42s} +{cat_counts[cat]:>4}")
    if dropped:
        print(f"  dropped (no country, no coords, no successor):   {len(dropped)}: {dropped[:10]}")

    return labels_rows, source_rows, [record]


def _create_symlink(features_dir: Path, alias_id: str, target_id: str) -> None:
    link = features_dir / f"{alias_id}.geojson"
    os.symlink(f"{target_id}.geojson", link)
