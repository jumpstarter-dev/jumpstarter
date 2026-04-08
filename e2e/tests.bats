JS_NAMESPACE="${JS_NAMESPACE:-jumpstarter-lab}"

# File to track bash wrapper process PIDs across tests
EXPORTER_PIDS_FILE="${BATS_RUN_TMPDIR:-/tmp}/exporter_pids.txt"

# Directory for exporter log files
EXPORTER_LOGS_DIR="${BATS_RUN_TMPDIR:-/tmp}/exporter_logs"

setup_file() {
  # Initialize the PIDs file at the start of all tests
  echo "" > "$EXPORTER_PIDS_FILE"
  # Create directory for exporter logs
  mkdir -p "$EXPORTER_LOGS_DIR"
}

setup() {
  bats_load_library bats-support
  bats_load_library bats-assert

  bats_require_minimum_version 1.5.0

  # Write test markers to exporter log files for easier correlation
  local marker="=== TEST START: ${BATS_TEST_NAME} @ $(date -Iseconds) ==="
  for logfile in "$EXPORTER_LOGS_DIR"/test-exporter-*.log; do
    if [ -f "$logfile" ]; then
      echo "$marker" >> "$logfile"
    fi
  done
}

# Dump debug logs when a test fails
teardown() {
  if [ "$BATS_TEST_COMPLETED" != 1 ]; then
    echo "" >&2
    echo "========================================" >&2
    echo "TEST FAILED: ${BATS_TEST_NAME}" >&2
    echo "========================================" >&2

    echo "" >&2
    echo "--- Exporter logs (test-exporter-oidc) ---" >&2
    if [ -f "$EXPORTER_LOGS_DIR/test-exporter-oidc.log" ]; then
      tail -250 "$EXPORTER_LOGS_DIR/test-exporter-oidc.log" >&2
    else
      echo "(no log file found)" >&2
    fi

    echo "" >&2
    echo "--- Exporter logs (test-exporter-sa) ---" >&2
    if [ -f "$EXPORTER_LOGS_DIR/test-exporter-sa.log" ]; then
      tail -250 "$EXPORTER_LOGS_DIR/test-exporter-sa.log" >&2
    else
      echo "(no log file found)" >&2
    fi

    echo "" >&2
    echo "--- Exporter logs (test-exporter-legacy) ---" >&2
    if [ -f "$EXPORTER_LOGS_DIR/test-exporter-legacy.log" ]; then
      tail -250 "$EXPORTER_LOGS_DIR/test-exporter-legacy.log" >&2
    else
      echo "(no log file found)" >&2
    fi

    echo "" >&2
    echo "--- Controller logs (last 250 lines) ---" >&2
    # operator uses component=controller, helm uses control-plane=controller-manager
    kubectl -n "${JS_NAMESPACE}" logs -l component=controller --tail=250 2>&1 >&2 \
      || kubectl -n "${JS_NAMESPACE}" logs -l control-plane=controller-manager --tail=250 2>&1 >&2 || true

    echo "" >&2
    echo "--- Router logs (last 250 lines) ---" >&2
    # operator uses component=router, helm uses control-plane=controller-router
    kubectl -n "${JS_NAMESPACE}" logs -l component=router --tail=250 2>&1 >&2 \
      || kubectl -n "${JS_NAMESPACE}" logs -l control-plane=controller-router --tail=250 2>&1 >&2 || true

    echo "========================================" >&2
  fi
}

# teardown_file runs once after all tests complete (requires bats-core 1.5.0+)
teardown_file() {
  echo "" >&2
  echo "========================================" >&2
  echo "TEARDOWN_FILE RUNNING" >&2
  echo "========================================" >&2
  echo "=== Cleaning up exporter bash processes ===" >&2
  
  # Read PIDs from file
  if [ -f "$EXPORTER_PIDS_FILE" ]; then
    local pids=$(cat "$EXPORTER_PIDS_FILE" | tr '\n' ' ')
    echo "Tracked PIDs from file: $pids" >&2
    
    while IFS= read -r pid; do
      if [ -n "$pid" ]; then
        echo "Checking PID $pid..." >&2
        if ps -p "$pid" > /dev/null 2>&1; then
          echo "  Killing PID $pid" >&2
          kill -9 "$pid" 2>/dev/null || true
        else
          echo "  PID $pid already terminated" >&2
        fi
      fi
    done < "$EXPORTER_PIDS_FILE"
  else
    echo "No PIDs file found at $EXPORTER_PIDS_FILE" >&2
  fi
  
  echo "Checking for orphaned jmp processes..." >&2
  local orphans=$(pgrep -f "jmp run --exporter" 2>/dev/null | wc -l)
  echo "Found $orphans orphaned jmp processes" >&2
  
  # remove orphaned processes
  pkill -9 -f "jmp run --exporter" 2>/dev/null || true
  
  # Clean up the PIDs file
  rm -f "$EXPORTER_PIDS_FILE"
  
  echo "=== Cleanup complete ===" >&2
}

wait_for_exporter() {
  # After a lease operation the exporter disconnects from the controller and reconnects.
  # The disconnect can take a short while so let's avoid catching the pre-disconnect state.
  sleep 2

  # Wait for Online + Registered conditions AND exporterStatus=Available.
  # Online+Registered alone are never cleared between leases, so they can't detect
  # whether the exporter has finished cleaning up a previous lease. Checking
  # exporterStatus=Available ensures the exporter's serve() loop has fully processed
  # the lease-end and is ready to accept new leases. (See #425)
  _wait_for_single_exporter test-exporter-oidc &
  local pid1=$!
  _wait_for_single_exporter test-exporter-sa &
  local pid2=$!
  _wait_for_single_exporter test-exporter-legacy &
  local pid3=$!

  # Wait for all to complete and capture failures
  local rc=0
  wait "$pid1" || rc=$?
  wait "$pid2" || rc=$?
  wait "$pid3" || rc=$?
  return $rc
}

_wait_for_single_exporter() {
  local name="$1"
  local timeout=300  # 5 minutes
  local elapsed=0

  # First wait for the basic conditions (fast path for initial registration)
  kubectl -n "${JS_NAMESPACE}" wait --timeout "${timeout}s" --for=condition=Online --for=condition=Registered \
    "exporters.jumpstarter.dev/${name}"

  # Then poll until exporterStatus is Available (not leased or cleaning up)
  while [ $elapsed -lt $timeout ]; do
    local status
    status=$(kubectl -n "${JS_NAMESPACE}" get "exporters.jumpstarter.dev/${name}" \
      -o jsonpath='{.status.exporterStatus}' 2>/dev/null || echo "")
    if [ "$status" = "Available" ]; then
      return 0
    fi
    sleep 0.5
    elapsed=$((elapsed + 1))
  done

  echo "Timed out waiting for ${name} to reach Available status" >&2
  return 1
}

@test "login endpoint serves landing page" {
  # Check if login service exists

  run curl -s http://${LOGIN_ENDPOINT}
  assert_success

  # Verify the response is HTML with login instructions
  assert_output --partial "Jumpstarter"
  assert_output --partial "jmp login"

}

@test "can create clients with admin cli" {
  run jmp admin create client -n "${JS_NAMESPACE}" test-client-oidc     --unsafe --nointeractive \
    --oidc-username dex:test-client-oidc
  assert_success

  run jmp admin create client -n "${JS_NAMESPACE}" test-client-sa       --unsafe --nointeractive \
    --oidc-username dex:system:serviceaccount:"${JS_NAMESPACE}":test-client-sa
  assert_success

  run jmp admin create client -n "${JS_NAMESPACE}" test-client-legacy   --unsafe --save
  assert_success

  run jmp config client list -o yaml
  assert_success
  assert_output --partial "test-client-legacy"
}

@test "can create exporters with admin cli" {
  run jmp admin create exporter -n "${JS_NAMESPACE}" test-exporter-oidc   --nointeractive \
    --oidc-username dex:test-exporter-oidc \
    --label example.com/board=oidc
  assert_success

  run jmp admin create exporter -n "${JS_NAMESPACE}" test-exporter-sa     --nointeractive \
    --oidc-username dex:system:serviceaccount:"${JS_NAMESPACE}":test-exporter-sa \
    --label example.com/board=sa
  assert_success

  run jmp admin create exporter -n "${JS_NAMESPACE}" test-exporter-legacy --save \
    --label example.com/board=legacy
  assert_success

  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"e2e/exporters/exporter.yaml\")" \
    /etc/jumpstarter/exporters/test-exporter-legacy.yaml
  run jmp config exporter list -o yaml
  assert_success
  assert_output --partial "test-exporter-legacy"
}

@test "can login with oidc test-client-oidc" {
  run jmp login --client test-client-oidc \
    --endpoint "$ENDPOINT" --namespace "${JS_NAMESPACE}" --name test-client-oidc \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --username test-client-oidc@example.com --password password --unsafe
  assert_success
  run jmp config client list -o yaml
  assert_success
  assert_output --partial "test-client-oidc"
}

@test "can login with oidc test-client-oidc-provisioning" {
  run jmp login --client test-client-oidc-provisioning-example-com \
    --endpoint "$ENDPOINT" --namespace "${JS_NAMESPACE}" --name="" \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --username test-client-oidc-provisioning@example.com --password password --unsafe
  assert_success
  run jmp config client list -o yaml
  assert_success
  assert_output --partial "test-client-oidc-provisioning-example-com"
}

@test "can login with oidc test-client-sa" {
  run jmp login --client test-client-sa \
    --endpoint "$ENDPOINT" --namespace "${JS_NAMESPACE}" --name test-client-sa \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --connector-id kubernetes \
    --token $(kubectl create -n "${JS_NAMESPACE}" token test-client-sa) --unsafe
  assert_success
  run jmp config client list -o yaml
  assert_success
  assert_output --partial "test-client-sa"
}

@test "can login with oidc test-exporter-oidc" {
  run jmp login --exporter test-exporter-oidc --name test-exporter-oidc \
    --endpoint "$ENDPOINT" --namespace "${JS_NAMESPACE}" \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --username test-exporter-oidc@example.com --password password
  assert_success
  # add the mock export paths to those files
  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"e2e/exporters/exporter.yaml\")" \
    /etc/jumpstarter/exporters/test-exporter-oidc.yaml
  run jmp config exporter list -o yaml
  assert_success
  assert_output --partial "test-exporter-oidc"

}

@test "can login with oidc test-exporter-sa" {
  run jmp login --exporter test-exporter-sa \
    --endpoint "$ENDPOINT" --namespace "${JS_NAMESPACE}" --name test-exporter-sa \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --connector-id kubernetes \
    --token $(kubectl create -n "${JS_NAMESPACE}" token test-exporter-sa)
  assert_success

  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"e2e/exporters/exporter.yaml\")" \
    /etc/jumpstarter/exporters/test-exporter-oidc.yaml
  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"e2e/exporters/exporter.yaml\")" \
    /etc/jumpstarter/exporters/test-exporter-sa.yaml
  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"e2e/exporters/exporter.yaml\")" \
    /etc/jumpstarter/exporters/test-exporter-legacy.yaml

  run jmp config exporter list -o yaml
  assert_success
  assert_output --partial "test-exporter-sa"
}

@test "can login with simplified login" {
  # This test only works with operator-based deployment, which deploys the CA ConfigMap
  if [ "${METHOD:-}" != "operator" ]; then
    skip "CA certificate injection only configured with operator deployment (METHOD=$METHOD)"
  fi

  jmp config client   delete test-client-oidc

  run jmp login test-client-oidc@${LOGIN_ENDPOINT} --insecure-login-http \
    --username test-client-oidc@example.com --password password --unsafe
  assert_success

  # Verify CA certificate is populated in client config
  local client_config="${HOME}/.config/jumpstarter/clients/test-client-oidc.yaml"
  run test -f "$client_config"
  assert_success
  run go run github.com/mikefarah/yq/v4@latest '.tls.ca' "$client_config"
  assert_success
  refute_output ""
  refute_output "null"
  echo "Client config has CA certificate populated"

  # Verify the new client is set as the default (marked with * in CURRENT column)
  run jmp config client list
  assert_success
  assert_line --regexp '^[[:space:]]*\*[[:space:]]+test-client-oidc[[:space:]]'
  echo "Client test-client-oidc is set as default"
}

@test "can run exporters" {
  cat <<EOF | bash 3>&- &
while true; do
  jmp run --exporter test-exporter-oidc >> "$EXPORTER_LOGS_DIR/test-exporter-oidc.log" 2>&1
done
EOF
  echo "$!" >> "$EXPORTER_PIDS_FILE"

  cat <<EOF | bash 3>&- &
while true; do
  jmp run --exporter test-exporter-sa >> "$EXPORTER_LOGS_DIR/test-exporter-sa.log" 2>&1
done
EOF
  echo "$!" >> "$EXPORTER_PIDS_FILE"

  cat <<EOF | bash 3>&- &
while true; do
  jmp run --exporter test-exporter-legacy >> "$EXPORTER_LOGS_DIR/test-exporter-legacy.log" 2>&1
done
EOF
  echo "$!" >> "$EXPORTER_PIDS_FILE"

  wait_for_exporter
}

@test "can specify client config only using environment variables" {
  wait_for_exporter

  # we feed the namespace into JMP_NAMESPACE along with all the other client details
  # to verify that the client can operate without a config file
  JMP_NAMESPACE="${JS_NAMESPACE}" \
  JMP_DRIVERS_ALLOW="*" \
  JMP_NAME=test-client-legacy \
  JMP_ENDPOINT=$(kubectl get clients.jumpstarter.dev -n "${JS_NAMESPACE}" test-client-legacy -o 'jsonpath={.status.endpoint}') \
  JMP_TOKEN=$(kubectl get secrets -n "${JS_NAMESPACE}" test-client-legacy-client -o 'jsonpath={.data.token}' | base64 -d) \
  jmp shell --selector example.com/board=oidc j power on
}

@test "legacy client config contains CA certificate and works with secure TLS" {
  # This test only works with operator-based deployment, which creates the CA ConfigMap
  if [ "${METHOD:-}" != "operator" ]; then
    skip "CA certificate injection only available with operator deployment (METHOD=$METHOD)"
  fi

  wait_for_exporter

  # Get the config file path from jmp (clients are saved to ~/.config/jumpstarter/clients/)
  local config_file="${HOME}/.config/jumpstarter/clients/test-client-legacy.yaml"
  run test -f "$config_file"
  assert_success

  # Check that tls.ca field exists and is not empty
  run go run github.com/mikefarah/yq/v4@latest '.tls.ca' "$config_file"
  assert_success
  # The CA should be a non-empty base64-encoded string
  refute_output ""
  refute_output "null"

  # Test that the client works WITHOUT JUMPSTARTER_GRPC_INSECURE set
  # This proves the CA certificate is being used for TLS verification
  run env -u JUMPSTARTER_GRPC_INSECURE jmp get exporters --client test-client-legacy -o yaml
  assert_success
  # Should see the legacy exporter in the output
  assert_output --partial "test-exporter-legacy"
}

@test "can operate on leases" {
  wait_for_exporter

  jmp config client use test-client-oidc

  jmp create lease     --selector example.com/board=oidc --duration 1d
  jmp get    leases
  jmp get    exporters

  # Verify label selector filtering works (regression test for issue #36)
  run jmp get leases --selector example.com/board=oidc -o yaml
  assert_success
  assert_output --partial "example.com/board=oidc"

  run jmp get leases --selector example.com/board=doesnotexist
  assert_success
  assert_output "No resources found."

  # Test complex selectors with matchExpressions (regression test for server-side over-filtering)
  # Use 'sa' exporter since 'oidc' is already leased above. The '!nonexistent' is a matchExpression
  # that will always be true (label doesn't exist on exporters), allowing the lease to match.
  jmp create lease --selector 'example.com/board=sa,!nonexistent' --duration 1d

  # Partial match: filter with just matchLabels (subset) → expecting a match
  run jmp get leases --selector 'example.com/board=sa' -o yaml
  assert_success
  assert_output --partial "example.com/board=sa"

  # Partial match: filter with just matchExpressions (subset) → expecting a match
  # This specifically tests client-side filtering of matchExpressions
  run jmp get leases --selector '!nonexistent' -o yaml
  assert_success
  assert_output --partial "!nonexistent"

  # Non-matching matchExpressions → expecting no match with current implementation
  # where we're filtering against the original lease request
  run jmp get leases --selector 'example.com/board=sa,!production'
  assert_success
  assert_output "No resources found."

  # Filter asks for more than lease has → expecting no match
  run jmp get leases --selector 'example.com/board=sa,!nonexistent,region=us'
  assert_success
  assert_output "No resources found."

  jmp delete leases    --all
}

@test "paginated lease listing returns all leases" {
  wait_for_exporter

  jmp config client use test-client-oidc

  for i in $(seq 1 101); do
    jmp create lease --selector example.com/board=oidc --duration 1d
  done

  run jmp get leases -o yaml
  assert_success

  local count
  count=$(echo "$output" | grep -c '^ *name:')
  [ "$count" -eq 101 ]

  jmp delete leases --all
}

@test "paginated exporter listing returns all exporters" {
  wait_for_exporter

  jmp config client use test-client-oidc

  for i in $(seq 1 101); do
    jmp admin create exporter -n "${JS_NAMESPACE}" "pagination-exp-${i}" --nointeractive \
      -l pagination=true --oidc-username "dex:pagination-exp-${i}"
  done

  run jmp get exporters --selector pagination=true -o yaml
  assert_success

  local count
  count=$(echo "$output" | grep -c '^ *name:')
  [ "$count" -eq 101 ]

  for i in $(seq 1 101); do
    jmp admin delete exporter --namespace "${JS_NAMESPACE}" "pagination-exp-${i}" --delete
  done
}

@test "can transfer lease to another client" {
  wait_for_exporter

  jmp config client use test-client-oidc

  # Create a lease owned by test-client-oidc
  run jmp create lease --selector example.com/board=oidc --duration 1d -o yaml
  assert_success
  LEASE_NAME=$(echo "$output" | go run github.com/mikefarah/yq/v4@latest '.name')

  # Wait for the lease to become active
  kubectl -n "${JS_NAMESPACE}" wait --timeout 60s --for=condition=Ready \
    leases.jumpstarter.dev/"$LEASE_NAME"

  # Transfer the lease to test-client-legacy
  run jmp update lease "$LEASE_NAME" --to-client test-client-legacy -o yaml
  assert_success
  assert_output --partial "test-client-legacy"

  # Delete as the new owner
  jmp delete leases --client test-client-legacy --all
}

@test "can lease and connect to exporters" {
  wait_for_exporter

  jmp shell --client test-client-oidc   --selector example.com/board=oidc   j power on
  jmp shell --client test-client-sa     --selector example.com/board=sa     j power on
  jmp shell --client test-client-legacy --selector example.com/board=legacy j power on

  wait_for_exporter
  jmp shell --client test-client-oidc-provisioning-example-com --selector example.com/board=oidc j power on
}

@test "can lease and connect to exporters by name" {
  wait_for_exporter

  jmp shell --client test-client-oidc   --name test-exporter-oidc   j power on
  jmp shell --client test-client-sa     --name test-exporter-sa     j power on
  jmp shell --client test-client-legacy --name test-exporter-legacy j power on

  # Reusing the same exporter immediately can be flaky while it reconnects.
  wait_for_exporter

  # --name and --selector together should work when they match.
  jmp shell --client test-client-oidc --name test-exporter-oidc --selector example.com/board=oidc j power on
}

@test "fails fast when requesting non-existent exporter by name" {
  wait_for_exporter

  # Strict behavior: missing named exporter should become Unsatisfiable and fail quickly.
  # If controller returns Pending here, this command will likely hit timeout (exit 124).
  run timeout 20s jmp shell --client test-client-oidc --name test-exporter-does-not-exist j power on
  assert_failure
  [ "$status" -ne 124 ]
  assert_output --partial "cannot be satisfied"
}

@test "can get crds with admin cli" {
  jmp admin get client --namespace "${JS_NAMESPACE}"
  jmp admin get exporter --namespace "${JS_NAMESPACE}"
  jmp admin get lease --namespace "${JS_NAMESPACE}"
}

@test "can delete clients with admin cli" {
  kubectl -n "${JS_NAMESPACE}" get secret test-client-oidc-client
  kubectl -n "${JS_NAMESPACE}" get clients.jumpstarter.dev/test-client-oidc
  kubectl -n "${JS_NAMESPACE}" get clients.jumpstarter.dev/test-client-sa
  kubectl -n "${JS_NAMESPACE}" get clients.jumpstarter.dev/test-client-legacy

  jmp admin delete client --namespace "${JS_NAMESPACE}" test-client-oidc   --delete
  jmp admin delete client --namespace "${JS_NAMESPACE}" test-client-sa     --delete
  jmp admin delete client --namespace "${JS_NAMESPACE}" test-client-legacy --delete

  run ! kubectl -n "${JS_NAMESPACE}" get secret test-client-oidc-client
  run ! kubectl -n "${JS_NAMESPACE}" get clients.jumpstarter.dev/test-client-oidc
  run ! kubectl -n "${JS_NAMESPACE}" get clients.jumpstarter.dev/test-client-sa
  run ! kubectl -n "${JS_NAMESPACE}" get clients.jumpstarter.dev/test-client-legacy
}

@test "can delete exporters with admin cli" {
  kubectl -n "${JS_NAMESPACE}" get secret test-exporter-oidc-exporter
  kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/test-exporter-oidc
  kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/test-exporter-sa
  kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/test-exporter-legacy

  jmp admin delete exporter --namespace "${JS_NAMESPACE}" test-exporter-oidc   --delete
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" test-exporter-sa     --delete
  jmp admin delete exporter --namespace "${JS_NAMESPACE}" test-exporter-legacy --delete

  run ! kubectl -n "${JS_NAMESPACE}" get secret test-exporter-oidc-exporter
  run ! kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/test-exporter-oidc
  run ! kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/test-exporter-sa
  run ! kubectl -n "${JS_NAMESPACE}" get exporters.jumpstarter.dev/test-exporter-legacy
}
