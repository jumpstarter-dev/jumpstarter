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
# Verify the observability configuration docs keep the Phase 5 contract: the
# pod discovery labels every shipper selects on, and no references to the
# removed operator-owned shipper API, condition, or ConfigMap.
#
# Only checks that encode a decision belong here. Whether a shipper example
# works is a property of the example, not of its prose: validate it with the
# shipper's own tooling when you change it.

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
config_docs="$repo_root/docs/source/getting-started/configuration"
doc="$config_docs/observability.md"

status=0

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  status=1
}

require_in() {
  local file=$1
  local description=$2
  local pattern=$3

  if ! grep -qF -- "$pattern" "$file"; then
    fail "$(realpath --relative-to="$repo_root" "$file"): missing $description"
  fi
}

require() {
  require_in "$doc" "$1" "$2"
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

# Shipper contract: every shipper configuration selects on these exact labels.
require 'log-scrape discovery label' 'jumpstarter.dev/log-scrape="true"'
require 'log-format discovery label' 'jumpstarter.dev/log-format="json"'

# Phase 6 starter alert rules contract.
require 'PrometheusRulesReady condition' 'PrometheusRulesReady'
require 'starter alert rules field' 'prometheusRules'

# Phase 6 dashboard and datasource contract: the page exists and names the
# Perses API surface it depends on. Wording, vendor versions and internal test
# names are deliberately not pinned.
require 'link to dashboard docs' 'dashboards-alerting.md'
dash_doc="$config_docs/dashboards-alerting.md"
if [ ! -f "$dash_doc" ]; then
  fail "$(realpath --relative-to="$repo_root" "$dash_doc" 2>/dev/null || printf '%s' "$dash_doc"): file not found"
else
  require_in "$dash_doc" 'dashboards enablement field' 'dashboards: true'
  require_in "$dash_doc" 'Perses instance selector field' 'persesInstanceSelector'
  require_in "$dash_doc" 'global datasource example' 'PersesGlobalDatasource'
  require_in "$dash_doc" 'project datasource example' 'PersesDatasource'
  require_in "$dash_doc" 'Prometheus datasource kind' 'PrometheusDatasource'
  require_in "$dash_doc" 'Loki datasource kind' 'LokiDatasource'
  require_in "$dash_doc" 'DashboardsReady condition' 'DashboardsReady'
fi

# The optional Grafana sample must exist, parse, and stay importable.
grafana_sample="$repo_root/docs/source/_static/jumpstarter-grafana-dashboard.json"
if [ ! -f "$grafana_sample" ]; then
  fail "$(realpath --relative-to="$repo_root" "$grafana_sample" 2>/dev/null || printf '%s' "$grafana_sample"): file not found"
elif ! command -v python3 >/dev/null 2>&1; then
  fail 'python3 is required to validate the Grafana dashboard sample'
elif ! python3 - "$grafana_sample" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    dashboard = json.load(handle)

problems = []
if dashboard.get("uid") != "jumpstarter-overview":
    problems.append("uid must be jumpstarter-overview")
panels = dashboard.get("panels") or []
if len(panels) != 10:
    problems.append(f"expected 10 panels, found {len(panels)}")
for panel in panels:
    if not panel.get("title") or not panel.get("type"):
        problems.append(f"panel {panel.get('id')} is missing a title or type")
    if not (panel.get("datasource") or {}).get("uid"):
        problems.append(f"panel {panel.get('title')!r} has no datasource uid")
    for target in panel.get("targets") or []:
        if not (target.get("expr") or "").strip():
            problems.append(f"panel {panel.get('title')!r} has an empty expression")

if problems:
    print("\n".join(problems), file=sys.stderr)
    sys.exit(1)
PY
then
  fail 'Grafana dashboard sample is not importable'
fi

# The operator no longer owns shipper configuration: neither the API, the
# condition, nor the generated ConfigMap may reappear in the configuration docs.
forbid 'logging.shipper API field' 'logging.shipper'
forbid 'shipper type API field' 'shipper.type'
forbid 'LogShipperReady condition' 'LogShipperReady'
forbid 'generated shipper ConfigMap name' 'jumpstarter-log-shipper'

if [ "$status" -ne 0 ]; then
  exit 1
fi

printf 'observability docs contract verified\n'
