#!/usr/bin/env bats
# Compatibility E2E tests: Old Controller (v0.7.x) + New Client/Exporter
#
# Tests that the new client and exporter code works correctly against
# an older controller version that doesn't support hooks protocol changes.
# Also includes the client-started-before-exporter scenario.

JS_NAMESPACE="${JS_NAMESPACE:-jumpstarter-lab}"

# File to track bash wrapper process PIDs across tests
COMPAT_PIDS_FILE="${BATS_RUN_TMPDIR:-/tmp}/compat_old_ctrl_pids.txt"

setup_file() {
  # Initialize the PIDs file at the start of all tests
  echo "" > "$COMPAT_PIDS_FILE"
}

setup() {
  bats_load_library bats-support
  bats_load_library bats-assert

  bats_require_minimum_version 1.5.0
}

teardown_file() {
  echo "" >&2
  echo "========================================" >&2
  echo "COMPAT OLD-CONTROLLER TEARDOWN RUNNING" >&2
  echo "========================================" >&2

  # Kill tracked PIDs
  if [ -f "$COMPAT_PIDS_FILE" ]; then
    while IFS= read -r pid; do
      if [ -n "$pid" ]; then
        if ps -p "$pid" > /dev/null 2>&1; then
          echo "  Killing PID $pid" >&2
          kill -9 "$pid" 2>/dev/null || true
        fi
      fi
    done < "$COMPAT_PIDS_FILE"
  fi

  # Kill any orphaned jmp processes for compat exporters
  pkill -9 -f "jmp run --exporter compat-" 2>/dev/null || true

  # Clean up the PIDs file
  rm -f "$COMPAT_PIDS_FILE"

  # Clean up CRDs (best effort)
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-client --delete 2>/dev/null || true
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-exporter --delete 2>/dev/null || true
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-client-wait --delete 2>/dev/null || true
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-exporter-wait --delete 2>/dev/null || true

  echo "=== Compat old-controller cleanup complete ===" >&2
}

wait_for_compat_exporter() {
  # Brief delay to avoid catching pre-disconnect state
  sleep 2
  kubectl -n "${JS_NAMESPACE}" wait --timeout 5m \
    --for=condition=Online --for=condition=Registered \
    exporters.jumpstarter.dev/compat-exporter
}

# ============================================================================
# Core compatibility tests: Old Controller + New Client/Exporter
# ============================================================================

@test "compat-old-ctrl: can create client with admin cli" {
  run jmp admin create client -n "${JS_NAMESPACE}" compat-client --unsafe --save
  assert_success
}

@test "compat-old-ctrl: can create exporter with admin cli" {
  run jmp admin create exporter -n "${JS_NAMESPACE}" compat-exporter --save \
    --label example.com/board=compat
  assert_success

  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"e2e/exporters/exporter.yaml\")" \
    /etc/jumpstarter/exporters/compat-exporter.yaml
}

@test "compat-old-ctrl: new exporter registers with old controller" {
  cat <<EOF | bash 3>&- &
while true; do
  jmp run --exporter compat-exporter
  sleep 2
done
EOF
  echo "$!" >> "$COMPAT_PIDS_FILE"

  wait_for_compat_exporter
}

@test "compat-old-ctrl: exporter shows as Online and Registered" {
  wait_for_compat_exporter

  run kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/compat-exporter \
    -o jsonpath='{.status.conditions[?(@.type=="Online")].status}'
  assert_success
  assert_output "True"

  run kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/compat-exporter \
    -o jsonpath='{.status.conditions[?(@.type=="Registered")].status}'
  assert_success
  assert_output "True"
}

@test "compat-old-ctrl: new client can lease and connect through old controller" {
  wait_for_compat_exporter

  run jmp shell --client compat-client \
    --selector example.com/board=compat j power on
  assert_success
}

@test "compat-old-ctrl: can operate on leases through old controller" {
  wait_for_compat_exporter

  jmp config client use compat-client

  jmp create lease --selector example.com/board=compat --duration 1d
  jmp get leases
  jmp get exporters

  # Verify label selector filtering works
  run jmp get leases --selector example.com/board=compat -o yaml
  assert_success
  assert_output --partial "example.com/board=compat"

  run jmp get leases --selector example.com/board=doesnotexist
  assert_success
  assert_output "No resources found."

  jmp delete leases --all
}

@test "compat-old-ctrl: exporter stays Online after lease cycle" {
  # After lease operations, the new exporter sends ReportStatus with
  # new fields (status_version, previous_status, release_lease) that
  # the old controller doesn't understand. The exporter must remain Online.
  wait_for_compat_exporter

  run kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/compat-exporter \
    -o jsonpath='{.status.conditions[?(@.type=="Online")].status}'
  assert_success
  assert_output "True"

  run kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/compat-exporter \
    -o jsonpath='{.status.conditions[?(@.type=="Registered")].status}'
  assert_success
  assert_output "True"
}

# ============================================================================
# Client started before exporter
# ============================================================================

@test "compat-old-ctrl: client started before exporter connects" {
  # Stop any running compat exporters for a clean test
  pkill -9 -f "jmp run --exporter compat-exporter-wait" 2>/dev/null || true
  sleep 3

  # Create fresh resources for this test
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-exporter-wait --delete 2>/dev/null || true
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-client-wait --delete 2>/dev/null || true

  run jmp admin create client -n "${JS_NAMESPACE}" compat-client-wait --unsafe --save
  assert_success

  run jmp admin create exporter -n "${JS_NAMESPACE}" compat-exporter-wait --save \
    --label example.com/board=compat-wait
  assert_success

  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"e2e/exporters/exporter.yaml\")" \
    /etc/jumpstarter/exporters/compat-exporter-wait.yaml

  # Start client shell BEFORE exporter is running (in background)
  # The client should wait for a matching exporter to become available
  jmp shell --client compat-client-wait \
    --selector example.com/board=compat-wait j power on &
  local CLIENT_PID=$!

  # Wait a few seconds to ensure the client is actively waiting for lease fulfillment
  sleep 5

  # Verify the client is still waiting (hasn't exited yet)
  if ! kill -0 $CLIENT_PID 2>/dev/null; then
    wait $CLIENT_PID || true
    fail "Client exited before exporter was started"
  fi

  # Now start the exporter
  cat <<EOF | bash 3>&- &
while true; do
  jmp run --exporter compat-exporter-wait
  sleep 2
done
EOF
  echo "$!" >> "$COMPAT_PIDS_FILE"

  # Wait for the client command to complete (with timeout)
  local timeout=120
  local count=0
  while kill -0 $CLIENT_PID 2>/dev/null && [ $count -lt $timeout ]; do
    sleep 1
    count=$((count + 1))
  done

  # Check if client process completed
  if kill -0 $CLIENT_PID 2>/dev/null; then
    kill -9 $CLIENT_PID 2>/dev/null || true
    fail "Client shell timed out waiting for exporter (${timeout}s)"
  fi

  # Verify client exited successfully
  wait $CLIENT_PID
  local exit_code=$?
  [ $exit_code -eq 0 ]
}

# ============================================================================
# Cleanup
# ============================================================================

@test "compat-old-ctrl: cleanup resources" {
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-client --delete 2>/dev/null || true
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-exporter --delete 2>/dev/null || true
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-client-wait --delete 2>/dev/null || true
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-exporter-wait --delete 2>/dev/null || true
}
