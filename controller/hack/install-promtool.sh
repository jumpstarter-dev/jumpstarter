#!/usr/bin/env bash
# Install a pinned promtool for validating the generated starter PrometheusRule.
#
# Version and checksum are pinned; override PROMTOOL_VERSION/PROMTOOL_SHA256 only
# when intentionally upgrading.
set -euo pipefail

version="${PROMTOOL_VERSION:-3.5.0}"
sha256="${PROMTOOL_SHA256:-e811827af26d822afb09a4f28314f61b618b12cff5369835a67f674d8b46f39a}"

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/../.." && pwd)
base="${RUNNER_TEMP:-$repo_root/.opencode/tmp}"
dest="$base/promtool-${version}"
archive="$base/prometheus-${version}.linux-amd64.tar.gz"

mkdir -p "$dest"
if [ ! -x "$dest/promtool" ]; then
  curl -fsSL -o "$archive" \
    "https://github.com/prometheus/prometheus/releases/download/v${version}/prometheus-${version}.linux-amd64.tar.gz"
  echo "${sha256}  ${archive}" | sha256sum -c -
  tar -xzf "$archive" -C "$dest" --strip-components=1
fi

if [ -n "${GITHUB_PATH:-}" ]; then
  echo "$dest" >>"$GITHUB_PATH"
else
  echo "promtool installed at $dest/promtool"
fi
