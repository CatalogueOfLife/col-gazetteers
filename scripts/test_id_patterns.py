"""Validate every committed feature id against the backend's regex pattern.

The backend publishes one POSIX-style regex per gazetteer at

    https://api.checklistbank.org/vocab/gazetteer

(as the `pattern` property on each entry). This test fetches the live
vocabulary and verifies that:

  * every <prefix>/labels.tsv id matches its prefix's pattern
  * every <prefix>/features/*.geojson filename id (without extension) matches
  * the labels.tsv ids and the features/ filenames cover the same set

In addition, the iso 2-letter country ids are cross-checked against the
backend's full ISO 3166-1 enumeration at

    https://api.checklistbank.org/vocab/country

Backend countries missing from our build are reported (Natural Earth
upstream is the limiting factor — informational, not a failure). Our build
having a 2-letter id that is *not* in the backend's enum is a hard failure.

The `text` gazetteer is intentionally skipped (free text, no geometries).
The `teow` gazetteer is also skipped because it is not yet in
`Gazetteer.java`; if/when the backend adds it, this test will start checking
it automatically.

Run with `python scripts/test_id_patterns.py` (no test framework required —
exit code 0 on success, 1 on first failure, with a per-prefix summary).
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VOCAB_URL = "https://api.checklistbank.org/vocab/gazetteer"
COUNTRY_URL = "https://api.checklistbank.org/vocab/country"
# Prefixes shipped here that aren't yet in the backend's Gazetteer enum.
# (Empty as of teow's addition to Gazetteer.java; refill if a future prefix
# is built before the enum entry lands.)
EXTENSION_PREFIXES: set[str] = set()
# Prefixes in the enum that we don't store geometries for.
SKIP_PREFIXES = {"text"}


def fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def fetch_vocab() -> dict[str, dict]:
    """Return {prefix: vocab entry}. Each entry includes a `pattern` field
    once the backend exposes it."""
    return {entry["name"]: entry for entry in fetch_json(VOCAB_URL)}


def fetch_countries() -> dict[str, dict]:
    """Return {alpha2: country entry} from the backend's ISO 3166-1 vocab."""
    out: dict[str, dict] = {}
    for entry in fetch_json(COUNTRY_URL):
        code = (entry.get("alpha2") or entry.get("iso2LetterCode") or "").upper()
        if code:
            out[code] = entry
    return out


def check_iso_against_country_vocab(countries: dict[str, dict]) -> bool:
    """Compare our iso/ 2-letter country ids against the backend's ISO
    3166-1 enumeration. Returns True on success."""
    iso_dir = REPO_ROOT / "iso"
    if not iso_dir.is_dir():
        print("[iso countries] iso/ directory missing — skipping country cross-check")
        return True
    label_ids = read_labels(iso_dir / "labels.tsv")
    ours = {i for i in label_ids if len(i) == 2 and i.isupper()}
    theirs = set(countries.keys())

    extra_ours = sorted(ours - theirs)
    missing_ours = sorted(theirs - ours)

    if extra_ours:
        print(f"[iso countries] FAIL: {len(extra_ours)} alpha-2 code(s) shipped "
              f"here are NOT in /vocab/country: {extra_ours[:20]}")
    else:
        print(f"[iso countries] OK    ({len(ours)}/{len(theirs)} alpha-2 codes "
              f"present; all match the backend ISO 3166-1 enum)")

    if missing_ours:
        # Informational only — Natural Earth doesn't ship every ISO code.
        sample = sorted(
            (code, countries[code].get("name", "")) for code in missing_ours
        )[:15]
        print(f"[iso countries] note: {len(missing_ours)} backend countries "
              f"have no geometry here (upstream Natural Earth omission):")
        for code, name in sample:
            print(f"  - {code}  {name}")
        if len(missing_ours) > len(sample):
            print(f"  … and {len(missing_ours) - len(sample)} more")

    return not extra_ours


def read_labels(labels_tsv: Path) -> list[str]:
    if not labels_tsv.exists():
        return []
    ids = []
    with labels_tsv.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            tab = line.find("\t")
            ids.append(line if tab < 0 else line[:tab])
    return ids


def feature_ids(features_dir: Path) -> list[str]:
    return sorted(p.stem for p in features_dir.glob("*.geojson"))


def check_prefix(prefix: str, pattern: str | None) -> tuple[int, list[str]]:
    """Return (checked_count, failures)."""
    failures: list[str] = []
    prefix_dir = REPO_ROOT / prefix
    if not prefix_dir.is_dir():
        return 0, [f"directory missing: {prefix_dir}"]

    label_ids = read_labels(prefix_dir / "labels.tsv")
    feat_ids = feature_ids(prefix_dir / "features")

    # Coverage check first — these would point at a build bug regardless of
    # whether the pattern catches them.
    label_set, feat_set = set(label_ids), set(feat_ids)
    label_only = sorted(label_set - feat_set)[:5]
    feat_only  = sorted(feat_set - label_set)[:5]
    if label_only:
        failures.append(f"in labels.tsv but no feature file: {label_only}")
    if feat_only:
        failures.append(f"feature file with no labels.tsv entry: {feat_only}")

    if pattern is None:
        return len(label_set | feat_set), failures + [
            "no `pattern` field in the vocab response (backend not yet deployed?)"
        ]

    try:
        rx = re.compile(pattern)
    except re.error as e:
        return len(label_set | feat_set), failures + [
            f"invalid regex {pattern!r}: {e}"
        ]

    all_ids = sorted(label_set | feat_set)
    bad = [i for i in all_ids if not rx.fullmatch(i)]
    if bad:
        failures.append(f"{len(bad)} id(s) fail pattern /{pattern}/, e.g. {bad[:10]}")
    return len(all_ids), failures


def main() -> int:
    try:
        vocab = fetch_vocab()
    except Exception as e:
        print(f"!! could not fetch {VOCAB_URL}: {e}", file=sys.stderr)
        return 1
    try:
        countries = fetch_countries()
    except Exception as e:
        print(f"!! could not fetch {COUNTRY_URL}: {e}", file=sys.stderr)
        return 1

    # Prefixes we ship that aren't in the vocab yet (e.g. `teow`) — flag them
    # as skipped, don't fail the run.
    local_prefixes = sorted(
        p.name for p in REPO_ROOT.iterdir()
        if p.is_dir() and (p / "features").is_dir() and not p.name.startswith(".")
    )

    overall_ok = True
    # Count how many prefixes have a pattern set. If none do, assume the
    # backend hasn't deployed the `pattern` field yet and downgrade the
    # missing-pattern complaints to warnings (so CI stays green until the
    # deploy lands). A mix — some prefixes patterned, some not — IS a real
    # bug and should fail the run.
    prefixes_with_vocab = [
        p for p in local_prefixes
        if p not in SKIP_PREFIXES and p not in EXTENSION_PREFIXES and p in vocab
    ]
    patterns_present = sum(
        1 for p in prefixes_with_vocab if vocab[p].get("pattern")
    )
    pattern_field_deployed = patterns_present > 0

    for prefix in local_prefixes:
        if prefix in SKIP_PREFIXES:
            continue
        if prefix in EXTENSION_PREFIXES:
            print(f"[{prefix}] skipped (extension, not in backend Gazetteer enum yet)")
            continue
        entry = vocab.get(prefix)
        if entry is None:
            # Same logic as the missing-pattern case: while the backend
            # hasn't deployed the new vocab shape (no `pattern` field on
            # any prefix), we treat "prefix missing from vocab" as WARN
            # rather than FAIL — it's the same backend-deploy window.
            if not pattern_field_deployed:
                print(f"[{prefix}] WARN  (prefix shipped here but not yet in /vocab/gazetteer)")
            else:
                print(f"[{prefix}] FAIL: prefix is shipped here but missing from vocab")
                overall_ok = False
            continue
        pattern = entry.get("pattern")
        n, failures = check_prefix(prefix, pattern)
        # Distinguish "no pattern field yet" from real failures. Only the
        # former is downgraded; coverage / id-mismatch failures still fail
        # the run.
        real_failures = [
            f for f in failures
            if not f.startswith("no `pattern` field")
            or pattern_field_deployed
        ]
        if real_failures:
            overall_ok = False
            print(f"[{prefix}] FAIL ({n} ids checked, pattern {pattern!r})")
            for f in real_failures:
                print(f"  - {f}")
        elif failures:  # only the "no pattern field yet" warning
            print(f"[{prefix}] WARN  ({n} ids checked; pattern not yet in backend vocab)")
        else:
            print(f"[{prefix}] OK    ({n} ids match /{pattern}/)")

    # Anything in the vocab that we don't ship (excluding TEXT which has no
    # geometry by design) — informational, not a failure.
    expected_missing = SKIP_PREFIXES
    for name in sorted(vocab):
        if name not in local_prefixes and name not in expected_missing:
            print(f"[{name}] note: in backend vocab but not built here yet")

    # Cross-check ISO 3166-1 country coverage against /vocab/country.
    if not check_iso_against_country_vocab(countries):
        overall_ok = False

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
