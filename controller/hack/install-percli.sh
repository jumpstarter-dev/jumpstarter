#!/usr/bin/env bash
# Install a pinned percli and extract its plugin archives for offline dashboard
# validation. The operator test target expects PERCLI and PERCLI_PLUGINS.
set -euo pipefail

version="${PERCLI_VERSION:-0.53.1}"
sha256="${PERCLI_SHA256:-126aec7cad521abf1536d125790e0542f5a4fbc3770b5de6f1c32e922cec1456}"

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/../.." && pwd)
base="${RUNNER_TEMP:-$repo_root/.opencode/tmp}"
dest="$base/percli-${version}"
archive="$base/perses_${version}_linux_amd64.tar.gz"
plugins="$dest/plugins"

mkdir -p "$dest"
if [ ! -x "$dest/percli" ]; then
  curl -fsSL -o "$archive" \
    "https://github.com/perses/perses/releases/download/v${version}/perses_${version}_linux_amd64.tar.gz"
  echo "${sha256}  ${archive}" | sha256sum -c -
  tar -xzf "$archive" -C "$dest"
fi

if [ ! -d "$plugins" ]; then
  mkdir -p "$plugins"
  for archive_file in "$dest"/plugins-archive/*.tar.gz; do
    name=$(basename "$archive_file" .tar.gz)
    mkdir -p "$plugins/$name"
    tar -xzf "$archive_file" -C "$plugins/$name"
  done
fi

if [ -n "${GITHUB_ENV:-}" ]; then
  {
    echo "PERCLI=$dest/percli"
    echo "PERCLI_PLUGINS=$plugins"
  } >>"$GITHUB_ENV"
else
  echo "percli installed at $dest/percli"
  echo "export PERCLI=$dest/percli"
  echo "export PERCLI_PLUGINS=$plugins"
fi
