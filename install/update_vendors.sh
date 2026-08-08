#!/usr/bin/env bash

set -euo pipefail

# JTN-615: vendor file destinations are specified relative to the repo root
# (e.g. `src/static/styles/select2.min.css`), so the script MUST run with cwd
# set to the repo root regardless of how install.sh invokes it. install.sh
# calls us via `bash "$SCRIPT_DIR/update_vendors.sh"`, which does not change
# cwd — so we were writing to $PWD/src/static/... which only existed when the
# user happened to invoke install.sh from the repo root. In CI (Dockerfile
# WORKDIR = /InkyPi/install), the relative path resolved to a non-existent
# directory and every curl call failed with exit 23 ("Failure writing output
# to destination"). Anchor cwd to the repo root here so relative paths always
# resolve correctly.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Versions
SELECT2_VERSION="4.1.0-beta.1"
FULLCALENDAR_VERSION="6.1.17"
JQUERY_VERSION="3.6.0"
CHARTJS_VERSION="4.5.1"

# Define vendor files: name | url | output path | expected sha256
#
# Every other network fetch in this codebase pins a checksum (the Waveshare
# driver manifest, the wheelhouse tarball/wheels) — these CDN fetches didn't,
# despite TLS-only transport security doing nothing to detect tampered or
# compromised content served *by* the CDN itself. Pin the sha256 of each file
# at the version currently declared above; bump both together when upgrading
# a vendor version (recompute with `curl -fsSL <url> | shasum -a 256`).
declare -a VENDORS=(
  "Select2 CSS|https://cdnjs.cloudflare.com/ajax/libs/select2/${SELECT2_VERSION}/css/select2.min.css|src/static/styles/select2.min.css|907f4395f54e25a1da1181672f1a498e98b26f7bfc6dcb6c209a737472451e49"
  "Select2 JS|https://cdnjs.cloudflare.com/ajax/libs/select2/${SELECT2_VERSION}/js/select2.min.js|src/static/scripts/select2.min.js|9c04b5c034013c1a9ad5f9d9abcc1dd59e8237e3e09875cb15d328d20da961fd"
  "jQuery|https://code.jquery.com/jquery-${JQUERY_VERSION}.min.js|src/static/scripts/jquery.min.js|ff1523fb7389539c84c65aba19260648793bb4f5e29329d2ee8804bc37a3fe6e"
  "Chart JS|https://cdn.jsdelivr.net/npm/chart.js@${CHARTJS_VERSION}/dist/chart.umd.min.js|src/static/scripts/chart.js|48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a"
  "Fullcalendar JS|https://cdn.jsdelivr.net/npm/fullcalendar@${FULLCALENDAR_VERSION}/index.global.min.js|src/static/scripts/calendar.min.js|f9fa1addb8dea87e99616898f3422e6ddf931f097e80c031c3e0deafbce91074"
)

# sha256sum (Linux) vs shasum -a 256 (macOS dev shells). Pick whichever is present.
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo "ERROR: Neither sha256sum nor shasum available to verify vendor downloads." >&2
    exit 1
  fi
}

# Download each vendor file
for vendor in "${VENDORS[@]}"; do
  IFS='|' read -r name url output expected_sha256 <<< "$vendor"
  echo "Updating $name..."
  # JTN-534: --retry-all-errors retries write errors too (curl exit 23) which
  # bit us during the JTN-528 sim run. --retry-delay 2 spaces retries to avoid
  # hammering the CDN under flaky connectivity.
  #
  # Download to a temp file first so a corrupted/incorrect/tampered download
  # never overwrites a previously-good vendored file.
  tmp_file=$(mktemp "${output}.XXXXXX")
  if ! curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 \
      --connect-timeout 10 --max-time 120 "$url" -o "$tmp_file"; then
    rc=$?
    rm -f "$tmp_file"
    echo "  ✗ Failed to download $name (curl exit $rc)" >&2
    exit 1
  fi

  actual_sha256=$(sha256_of "$tmp_file")
  if [ "$actual_sha256" != "$expected_sha256" ]; then
    rm -f "$tmp_file"
    echo "  ✗ sha256 mismatch for $name" >&2
    echo "      expected: $expected_sha256" >&2
    echo "      actual:   $actual_sha256" >&2
    echo "    Refusing to install; if this vendor version was intentionally" >&2
    echo "    bumped, update the pinned sha256 in update_vendors.sh." >&2
    exit 1
  fi

  mv "$tmp_file" "$output"
  echo "  ✓ Downloaded and verified $output"
done

echo "All vendor files updated."
