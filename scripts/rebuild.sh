#!/usr/bin/env bash
# Rebuilds the gazetteers named as arguments and commits what changed. Called by
# the rebuild workflows, with `build.py` of each prefix limited to $BUILD_LIMIT.
#
# A gazetteer that fails keeps what is committed, so that a broken upstream only
# holds back its own data. The job still ends red to show it.

set -euo pipefail

failed=""
for prefix in "$@"; do
  echo "=== $prefix ==="
  if ! timeout "${BUILD_LIMIT:-120m}" python -u "scripts/$prefix/build.py"; then
    echo "::error::$prefix failed, keeping what is committed"
    git checkout -- "$prefix"
    git clean -fdq "$prefix"
    failed="$failed $prefix"
  fi
done

python -u scripts/build_overviews.py
# Only what changed, so that a new matplotlib doesn't commit every map.
for overview in */overview.geojson; do
  prefix="${overview%%/*}"
  if [ -n "$(git status --porcelain -- "$overview")" ]; then
    python -u scripts/build_maps.py "$prefix"
  fi
done
python -u scripts/test_id_patterns.py

# Detect changes that AREN'T just build.json timestamp churn. build.json's
# `built_at` / `downloaded_at` move on every run even when the source files are
# byte-identical, so committing those alone would create empty-content commits
# every month.
if [ -z "$(git status --porcelain -- . ':(exclude)*/build.json')" ]; then
  echo "No content changes — only build.json timestamps moved. Skipping commit."
else
  echo "Content changed. Staging everything (including refreshed build.json) and committing."
  git config user.name  "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git add -A
  # Summarise which prefixes changed for the commit message.
  changed_prefixes=$(git diff --cached --name-only \
      | awk -F/ '{print $1}' | sort -u \
      | grep -E '^(continent|costello|fao|gi|iho|iso|longhurst|mrgid|realm|tdwg|teow|wdpa)$' \
      | tr '\n' ',' | sed 's/,$//' || true)
  git commit -q -m "Auto-rebuild $(date -u +%Y-%m-%d): ${changed_prefixes:-unknown}" \
    -m "Triggered by ${GITHUB_EVENT_NAME:-hand}. Refreshed prefixes detected drift in upstream data or in the build scripts." \
    -m "Co-Authored-By: github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"
  # A rebuild takes hours, main may have moved on since the checkout.
  git pull -q --rebase
  git push -q
fi

if [ -n "$failed" ]; then
  echo "::error::failed to rebuild:$failed"
  exit 1
fi
