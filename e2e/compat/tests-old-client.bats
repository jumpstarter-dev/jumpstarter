#!/usr/bin/env bats
# Compatibility E2E tests: New Controller + Old Client/Exporter (v0.7.x)
#
# Tests that old client and exporter code (installed from PyPI) works
# correctly against the new controller. Verifies that old exporters
# are not incorrectly marked as offline by the new controller.
#
# Uses NEW jmp admin for setup (Kubernetes API) and OLD jmp ($OLD_JMP)
# for client/exporter operations (controller gRPC).

JS_NAMESPACE="${JS_NAMESPACE:-jumpstarter-lab}"
OLD_JMP="${OLD_JMP:-}"

# File to track bash wrapper process PIDs across tests
COMPAT_PIDS_FILE="${BATS_RUN_TMPDIR:-/tmp}/compat_old_client_pids.txt"

setup_file() {
  # Initialize the PIDs file at the start of all tests
  echo "" > "$COMPAT_PIDS_FILE"

  # Verify OLD_JMP is set and exists
  if [ -z "$OLD_JMP" ] || [ ! -x "$OLD_JMP" ]; then
    echo "ERROR: OLD_JMP not set or not executable: '$OLD_JMP'" >&2
    echo "Please run setup.sh with COMPAT_SCENARIO=old-client first." >&2
    exit 1
  fi

  echo "Using OLD_JMP: $OLD_JMP" >&2
  echo "OLD_JMP version:" >&2
  $OLD_JMP --version >&2 2>/dev/null || echo "(version flag not supported)" >&2
}

setup() {
  bats_load_library bats-support
  bats_load_library bats-assert

  bats_require_minimum_version 1.5.0
}

teardown_file() {
  echo "" >&2
  echo "========================================" >&2
  echo "COMPAT OLD-CLIENT TEARDOWN RUNNING" >&2
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

  # Kill any orphaned old jmp processes
  pkill -9 -f "jmp run --exporter compat-old-" 2>/dev/null || true

  # Clean up the PIDs file
  rm -f "$COMPAT_PIDS_FILE"

  # Clean up CRDs (use new admin CLI)
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-old-client --delete 2>/dev/null || true
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-old-exporter --delete 2>/dev/null || true
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-old-client-wait --delete 2>/dev/null || true
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-old-exporter-wait --delete 2>/dev/null || true

  echo "=== Compat old-client cleanup complete ===" >&2
}

wait_for_compat_exporter() {
  # Brief delay to avoid catching pre-disconnect state
  sleep 2
  kubectl -n "${JS_NAMESPACE}" wait --timeout 5m \
    --for=condition=Online --for=condition=Registered \
    exporters.jumpstarter.dev/compat-old-exporter
}

# ============================================================================
# Setup: Use NEW admin CLI to create resources
# ============================================================================

@test "compat-old-client: create resources" {
  run jmp admin create client -n "${JS_NAMESPACE}" compat-old-client --unsafe --save
  assert_success

  run jmp admin create exporter -n "${JS_NAMESPACE}" compat-old-exporter --save \
    --label example.com/board=compat-old
  assert_success

  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"e2e/exporters/exporter.yaml\")" \
    /etc/jumpstarter/exporters/compat-old-exporter.yaml
}

# ============================================================================
# Old exporter registration and Online status
# ============================================================================

@test "compat-old-client: old exporter registers with new controller" {
  cat <<EOF | bash 3>&- &
while true; do
  $OLD_JMP run --exporter compat-old-exporter
  sleep 2
done
EOF
  echo "$!" >> "$COMPAT_PIDS_FILE"

  wait_for_compat_exporter
}

@test "compat-old-client: old exporter shows as Online (not incorrectly offline)" {
  wait_for_compat_exporter

  # Key regression: the new controller must NOT mark old exporters offline
  # due to missing hooks protocol fields in ReportStatus.
  run kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/compat-old-exporter \
    -o jsonpath='{.status.conditions[?(@.type=="Online")].status}'
  assert_success
  assert_output "True"

  run kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/compat-old-exporter \
    -o jsonpath='{.status.conditions[?(@.type=="Registered")].status}'
  assert_success
  assert_output "True"
}

# ============================================================================
# Lease cycles: old client, new client, and Online status after leases
# ============================================================================

@test "compat-old-client: old client can connect through new controller" {
  wait_for_compat_exporter

  run $OLD_JMP shell --client compat-old-client \
    --selector example.com/board=compat-old j power on
  assert_success
}

@test "compat-old-client: old exporter stays Online after lease completes" {
  # After a lease cycle, the old exporter won't send the new ReportStatus
  # fields (status_version, previous_status, release_lease). The new controller
  # must not mark the exporter offline because of this.
  wait_for_compat_exporter

  run kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/compat-old-exporter \
    -o jsonpath='{.status.conditions[?(@.type=="Online")].status}'
  assert_success
  assert_output "True"
}

@test "compat-old-client: new client can connect to old exporter" {
  wait_for_compat_exporter

  run jmp shell --client compat-old-client \
    --selector example.com/board=compat-old j power on
  assert_success
}

@test "compat-old-client: old exporter still Online after multiple lease cycles" {
  # Catches accumulated state drift across lease transitions
  # when the controller doesn't receive new status fields.
  wait_for_compat_exporter

  run kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/compat-old-exporter \
    -o jsonpath='{.status.conditions[?(@.type=="Online")].status}'
  assert_success
  assert_output "True"

  run kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/compat-old-exporter \
    -o jsonpath='{.status.conditions[?(@.type=="Registered")].status}'
  assert_success
  assert_output "True"
}

# ============================================================================
# Client started before exporter
# ============================================================================

@test "compat-old-client: client started before exporter connects" {
  # Stop any running wait exporters for a clean test
  pkill -9 -f "jmp run --exporter compat-old-exporter-wait" 2>/dev/null || true
  sleep 3

  # Create fresh resources for this test
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-old-exporter-wait --delete 2>/dev/null || true
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-old-client-wait --delete 2>/dev/null || true

  run jmp admin create client -n "${JS_NAMESPACE}" compat-old-client-wait --unsafe --save
  assert_success

  run jmp admin create exporter -n "${JS_NAMESPACE}" compat-old-exporter-wait --save \
    --label example.com/board=compat-old-wait
  assert_success

  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"e2e/exporters/exporter.yaml\")" \
    /etc/jumpstarter/exporters/compat-old-exporter-wait.yaml

  # Start new client shell BEFORE old exporter is running (in background)
  # The client should wait for a matching exporter to become available
  jmp shell --client compat-old-client-wait \
    --selector example.com/board=compat-old-wait j power on &
  local CLIENT_PID=$!

  # Wait a few seconds to ensure the client is actively waiting for lease fulfillment
  sleep 5

  # Verify the client is still waiting (hasn't exited yet)
  if ! kill -0 $CLIENT_PID 2>/dev/null; then
    wait $CLIENT_PID || true
    fail "Client exited before exporter was started"
  fi

  # Now start the old exporter
  cat <<EOF | bash 3>&- &
while true; do
  $OLD_JMP run --exporter compat-old-exporter-wait
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

@test "compat-old-client: cleanup resources" {
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-old-client --delete 2>/dev/null || true
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-old-exporter --delete 2>/dev/null || true
  jmp admin delete client --namespace "${JS_NAMESPACE}" compat-old-client-wait --delete 2>/dev/null || true
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" compat-old-exporter-wait --delete 2>/dev/null || true
}
