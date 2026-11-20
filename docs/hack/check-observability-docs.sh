#!/usr/bin/env bash
#
# Copyright 2026 The Jumpstarter Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Verify the observability configuration docs keep the Phase 5 contract:
# discovery labels, the three shipper examples, the stream-label rules, the
# validation commands, and no references to the removed operator-owned
# shipper API or ConfigMap.

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
config_docs="$repo_root/docs/source/getting-started/configuration"
doc="$config_docs/observability.md"

status=0

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  status=1
}

require() {
  local description=$1
  local pattern=$2

  if ! grep -qF -- "$pattern" "$doc"; then
    fail "$(realpath --relative-to="$repo_root" "$doc"): missing $description"
  fi
}

forbid() {
  local description=$1
  local pattern=$2
  local matches
  local rc

  # grep exits 1 for "no match" and 2+ for a scan failure (unreadable file,
  # bad pattern). Treating 2 as "no match" would let the contract pass without
  # scanning every page.
  matches=$(grep -rnF --include='*.md' -- "$pattern" "$config_docs") && rc=0 || rc=$?
  if [ "$rc" -eq 0 ]; then
    fail "obsolete $description still documented:"
    printf '%s\n' "$matches" >&2
  elif [ "$rc" -ne 1 ]; then
    fail "failed to scan configuration docs for $description (grep exit $rc)"
  fi
}

if [ ! -f "$doc" ]; then
  fail "$(realpath --relative-to="$repo_root" "$doc" 2>/dev/null || printf '%s' "$doc"): file not found"
  exit 1
fi

# Pod discovery contract: the operator only labels pods and emits JSON stdout.
require 'log-scrape discovery label' 'jumpstarter.dev/log-scrape="true"'
require 'log-format discovery label' 'jumpstarter.dev/log-format="json"'
require 'statement that no shipper ConfigMap is generated' \
  'does not generate a shipper ConfigMap'

# Metrics Services use the component-first names from the Phase 5 cleanup.
require 'controller metrics Service name' '<name>-metrics-controller'
require 'router metrics Service name' '<name>-metrics-router'

# One worked example per supported shipper.
require 'Grafana Alloy example' '### Grafana Alloy'
require 'Vector example' '### Vector'
require 'Promtail example' '### Promtail'
require 'Alloy component example' 'loki.write'
require 'Vector source example' 'kubernetes_logs'
require 'Promtail discovery example' 'kubernetes_sd_configs'

# Stream-label rules the shipper configuration must implement. The backticks
# are literal prose in the pattern, not command substitution.
# shellcheck disable=SC2016
require 'stream-label guidance' \
  'promote `component`, `exporter`, and `namespace` to Loki stream labels'

# Validation commands for every documented shipper.
require 'Promtail validation command' '-check-syntax'
require 'Alloy validation command' 'alloy validate'
require 'Alloy formatting check' 'alloy fmt --test'
require 'Vector validation command' 'vector validate'

# The operator no longer owns shipper configuration: neither the API, the
# condition, nor the generated ConfigMap may reappear in the configuration docs.
forbid 'logging.shipper API field' 'logging.shipper'
forbid 'shipper type API field' 'shipper.type'
forbid 'LogShipperReady condition' 'LogShipperReady'
forbid 'generated shipper ConfigMap name' 'jumpstarter-log-shipper'
# No separate `{.data.` check: that literal also appears in unrelated JSONPath
# examples (for example a Secret inspection), and the ConfigMap-name check above
# already rejects references to the removed shipper ConfigMap.

if [ "$status" -ne 0 ]; then
  exit 1
fi

printf 'observability docs contract verified\n'
