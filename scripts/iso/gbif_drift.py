"""Watch GBIF's import.sh for changes to the steps the iso build replays.

The build replays `import_political()` of gbif/geocode's `database/import.sh`
and reads the files `import_mr_eez()` downloads. Both functions are pinned in
`gbif_import_political.sh` as they were at PINNED_COMMIT. When either changes
upstream, the rebuild warns, so someone can check whether the replay in
`political.py` or the files in `gbif_sources.py` need to follow.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

PINNED_COMMIT = "6a578ecefde0"
PINNED = Path(__file__).with_name("gbif_import_political.sh")
UPSTREAM_URL = "https://raw.githubusercontent.com/gbif/geocode/master/database/import.sh"
WATCHED = ("import_political", "import_mr_eez")

_MIRROR_FILE = re.compile(r"MapDataMirror/[0-9/]*([^/\s\"']+)")


def extract_function(script: str, name: str) -> str:
    """The text of shell function `name`, from `function name() {` to the first `}` alone on a line."""
    lines = script.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"function {name}() {{"):
            for j in range(i + 1, len(lines)):
                if lines[j] == "}":
                    return "\n".join(lines[i:j + 1]) + "\n"
            return "\n".join(lines[i:]) + "\n"
    return ""


def drift_warnings(pinned: str, upstream: str) -> list[str]:
    """One warning per watched function whose text differs, naming any mirror file it newly downloads."""
    warnings = []
    for name in WATCHED:
        old, new = extract_function(pinned, name), extract_function(upstream, name)
        if old == new:
            continue
        added = sorted(set(_MIRROR_FILE.findall(new)) - set(_MIRROR_FILE.findall(old)))
        files = f" It now downloads {', '.join(added)}." if added else ""
        warnings.append(
            f"GBIF's {name}() in import.sh changed since commit {PINNED_COMMIT}.{files} "
            f"Check whether the iso build needs the same change."
        )
    return warnings


def check_drift() -> list[str]:
    """Compare the pinned functions with gbif/geocode's master. A fetch failure is a warning too."""
    try:
        response = requests.get(UPSTREAM_URL, timeout=60, headers={"User-Agent": "col-gazetteers"})
        response.raise_for_status()
    except requests.RequestException as e:
        return [f"Could not fetch GBIF's import.sh to check for changes: {e}"]
    return drift_warnings(PINNED.read_text(encoding="utf-8"), response.text)
