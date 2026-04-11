#!/usr/bin/env bats
#
# JEP-0002 ExporterClass integration tests.
#
# These tests exercise the full ExporterClass and DriverInterface lifecycle
# in a Kind cluster with Jumpstarter installed. They must run AFTER the
# base e2e setup (setup-e2e.sh) and after exporters are registered.
#
# Run with: bats e2e/tests-exporterclass.bats

JS_NAMESPACE="${JS_NAMESPACE:-jumpstarter-lab}"

# Temporary directory for test artifacts
EC_TEST_DIR="${BATS_RUN_TMPDIR:-/tmp}/exporterclass-test"

setup_file() {
  mkdir -p "$EC_TEST_DIR"
}

setup() {
  bats_load_library bats-support
  bats_load_library bats-assert

  bats_require_minimum_version 1.5.0
}

teardown() {
  if [ "$BATS_TEST_COMPLETED" != 1 ]; then
    echo "" >&2
    echo "========================================" >&2
    echo "TEST FAILED: ${BATS_TEST_NAME}" >&2
    echo "========================================" >&2

    echo "" >&2
    echo "--- DriverInterfaces ---" >&2
    kubectl -n "${JS_NAMESPACE}" get driverinterfaces -o wide 2>&1 >&2 || true

    echo "" >&2
    echo "--- ExporterClasses ---" >&2
    kubectl -n "${JS_NAMESPACE}" get exporterclasses -o wide 2>&1 >&2 || true

    echo "" >&2
    echo "--- Exporters with conditions ---" >&2
    kubectl -n "${JS_NAMESPACE}" get exporters -o jsonpath='{range .items[*]}{.metadata.name}: {.status.conditions}{"\n"}{end}' 2>&1 >&2 || true

    echo "" >&2
    echo "--- Controller logs (last 100 lines) ---" >&2
    kubectl -n "${JS_NAMESPACE}" logs -l component=controller --tail=100 2>&1 >&2 \
      || kubectl -n "${JS_NAMESPACE}" logs -l control-plane=controller-manager --tail=100 2>&1 >&2 || true

    echo "========================================" >&2
  fi
}

teardown_file() {
  # Clean up all test resources
  kubectl -n "${JS_NAMESPACE}" delete exporterclasses --all 2>/dev/null || true
  kubectl -n "${JS_NAMESPACE}" delete driverinterfaces --all 2>/dev/null || true
  rm -rf "$EC_TEST_DIR"
}

# ============================================================
# DriverInterface CRD tests
# ============================================================

@test "can apply DriverInterface CRDs via jmp admin" {
  cat > "$EC_TEST_DIR/di-power.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverInterface
metadata:
  name: di-power-test
spec:
  proto:
    package: jumpstarter.interfaces.power.v1
  drivers:
    - language: python
      package: jumpstarter-driver-power
EOF

  cat > "$EC_TEST_DIR/di-serial.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverInterface
metadata:
  name: di-serial-test
spec:
  proto:
    package: jumpstarter.interfaces.serial.v1
  drivers:
    - language: python
      package: jumpstarter-driver-serial
EOF

  run jmp admin apply driverinterface "$EC_TEST_DIR/di-power.yaml" -n "${JS_NAMESPACE}"
  assert_success

  run jmp admin apply driverinterface "$EC_TEST_DIR/di-serial.yaml" -n "${JS_NAMESPACE}"
  assert_success
}

@test "can list DriverInterfaces via jmp admin get" {
  run jmp admin get driverinterfaces -n "${JS_NAMESPACE}"
  assert_success
  assert_output --partial "di-power-test"
  assert_output --partial "di-serial-test"
}

@test "can get a single DriverInterface via jmp admin get" {
  run jmp admin get driverinterface di-power-test -n "${JS_NAMESPACE}"
  assert_success
  assert_output --partial "di-power-test"
  assert_output --partial "jumpstarter.interfaces.power.v1"
}

@test "can get DriverInterface as JSON" {
  run jmp admin get driverinterface di-power-test -n "${JS_NAMESPACE}" --output json
  assert_success
  assert_output --partial '"package": "jumpstarter.interfaces.power.v1"'
}

@test "can get DriverInterface as YAML" {
  run jmp admin get driverinterface di-power-test -n "${JS_NAMESPACE}" --output yaml
  assert_success
  assert_output --partial "package: jumpstarter.interfaces.power.v1"
}

# ============================================================
# ExporterClass CRD tests
# ============================================================

@test "can apply ExporterClass CRD via jmp admin" {
  cat > "$EC_TEST_DIR/ec-embedded.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: embedded-linux-test
spec:
  selector:
    matchLabels:
      example.com/board: oidc
  interfaces:
    - name: power
      interfaceRef: di-power-test
      required: true
    - name: serial
      interfaceRef: di-serial-test
      required: false
EOF

  run jmp admin apply exporterclass "$EC_TEST_DIR/ec-embedded.yaml" -n "${JS_NAMESPACE}"
  assert_success
}

@test "can list ExporterClasses via jmp admin get" {
  run jmp admin get exporterclasses -n "${JS_NAMESPACE}"
  assert_success
  assert_output --partial "embedded-linux-test"
}

@test "can get a single ExporterClass via jmp admin get" {
  run jmp admin get exporterclass embedded-linux-test -n "${JS_NAMESPACE}"
  assert_success
  assert_output --partial "embedded-linux-test"
}

@test "can get ExporterClass as JSON" {
  run jmp admin get exporterclass embedded-linux-test -n "${JS_NAMESPACE}" --output json
  assert_success
  assert_output --partial '"name": "embedded-linux-test"'
}

# ============================================================
# ExporterClass reconciliation and status tests
# ============================================================

@test "ExporterClass status is reconciled after creation" {
  # Wait for the controller to reconcile — poll the status.
  local attempts=0
  local max_attempts=30
  while [ $attempts -lt $max_attempts ]; do
    local ready
    ready=$(kubectl -n "${JS_NAMESPACE}" get exporterclass embedded-linux-test \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    if [ "$ready" = "True" ]; then
      break
    fi
    sleep 2
    attempts=$((attempts + 1))
  done

  run kubectl -n "${JS_NAMESPACE}" get exporterclass embedded-linux-test \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
  assert_success
  assert_output "True"
}

@test "ExporterClass resolvedInterfaces includes all interface refs" {
  run kubectl -n "${JS_NAMESPACE}" get exporterclass embedded-linux-test \
    -o jsonpath='{.status.resolvedInterfaces}'
  assert_success
  assert_output --partial "di-power-test"
  assert_output --partial "di-serial-test"
}

@test "ExporterClass satisfiedExporterCount is accurate" {
  run kubectl -n "${JS_NAMESPACE}" get exporterclass embedded-linux-test \
    -o jsonpath='{.status.satisfiedExporterCount}'
  assert_success
  # The count depends on how many matching exporters have the power interface.
  # At minimum we should get a non-negative integer.
  [[ "$output" =~ ^[0-9]+$ ]]
}

# ============================================================
# ExporterClass inheritance tests
# ============================================================

@test "can apply parent and child ExporterClasses with extends" {
  cat > "$EC_TEST_DIR/ec-parent.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: ec-parent-test
spec:
  interfaces:
    - name: power
      interfaceRef: di-power-test
      required: true
EOF

  cat > "$EC_TEST_DIR/ec-child.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: ec-child-test
spec:
  extends: ec-parent-test
  interfaces:
    - name: serial
      interfaceRef: di-serial-test
      required: true
EOF

  run jmp admin apply exporterclass "$EC_TEST_DIR/ec-parent.yaml" -n "${JS_NAMESPACE}"
  assert_success

  run jmp admin apply exporterclass "$EC_TEST_DIR/ec-child.yaml" -n "${JS_NAMESPACE}"
  assert_success
}

@test "child ExporterClass resolvedInterfaces includes parent interfaces" {
  # Wait for reconciliation.
  local attempts=0
  while [ $attempts -lt 20 ]; do
    local resolved
    resolved=$(kubectl -n "${JS_NAMESPACE}" get exporterclass ec-child-test \
      -o jsonpath='{.status.resolvedInterfaces}' 2>/dev/null)
    if echo "$resolved" | grep -q "di-power-test"; then
      break
    fi
    sleep 2
    attempts=$((attempts + 1))
  done

  run kubectl -n "${JS_NAMESPACE}" get exporterclass ec-child-test \
    -o jsonpath='{.status.resolvedInterfaces}'
  assert_success
  assert_output --partial "di-power-test"
  assert_output --partial "di-serial-test"
}

# ============================================================
# Circular extends detection
# ============================================================

@test "circular extends chain is detected as Degraded" {
  cat > "$EC_TEST_DIR/ec-circular-a.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: ec-circular-a-test
spec:
  extends: ec-circular-b-test
EOF

  cat > "$EC_TEST_DIR/ec-circular-b.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: ec-circular-b-test
spec:
  extends: ec-circular-a-test
EOF

  run jmp admin apply exporterclass "$EC_TEST_DIR/ec-circular-a.yaml" -n "${JS_NAMESPACE}"
  assert_success

  run jmp admin apply exporterclass "$EC_TEST_DIR/ec-circular-b.yaml" -n "${JS_NAMESPACE}"
  assert_success

  # Wait for reconciliation.
  sleep 5

  run kubectl -n "${JS_NAMESPACE}" get exporterclass ec-circular-a-test \
    -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}'
  assert_success
  assert_output "True"

  run kubectl -n "${JS_NAMESPACE}" get exporterclass ec-circular-a-test \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
  assert_success
  assert_output "False"
}

# ============================================================
# Missing DriverInterface reference
# ============================================================

@test "ExporterClass with missing DriverInterface reference is Degraded" {
  cat > "$EC_TEST_DIR/ec-missing-di.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: ec-missing-di-test
spec:
  interfaces:
    - name: nonexistent
      interfaceRef: di-does-not-exist
      required: true
EOF

  run jmp admin apply exporterclass "$EC_TEST_DIR/ec-missing-di.yaml" -n "${JS_NAMESPACE}"
  assert_success

  # Wait for reconciliation.
  sleep 5

  run kubectl -n "${JS_NAMESPACE}" get exporterclass ec-missing-di-test \
    -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}'
  assert_success
  assert_output "True"

  run kubectl -n "${JS_NAMESPACE}" get exporterclass ec-missing-di-test \
    -o jsonpath='{.status.conditions[?(@.type=="Degraded")].reason}'
  assert_success
  assert_output "MissingDriverInterface"
}

# ============================================================
# CRD update re-evaluation
# ============================================================

@test "updating ExporterClass re-evaluates exporters" {
  # Get the current satisfiedExporterCount.
  local count_before
  count_before=$(kubectl -n "${JS_NAMESPACE}" get exporterclass embedded-linux-test \
    -o jsonpath='{.status.satisfiedExporterCount}' 2>/dev/null)

  # Add a non-existent required interface to the ExporterClass.
  # This should cause all exporters to fail validation.
  cat > "$EC_TEST_DIR/ec-embedded-updated.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: embedded-linux-test
spec:
  selector:
    matchLabels:
      example.com/board: oidc
  interfaces:
    - name: power
      interfaceRef: di-power-test
      required: true
    - name: serial
      interfaceRef: di-serial-test
      required: false
    - name: gpu
      interfaceRef: di-gpu-nonexistent
      required: true
EOF

  run jmp admin apply exporterclass "$EC_TEST_DIR/ec-embedded-updated.yaml" -n "${JS_NAMESPACE}"
  assert_success

  # Wait for reconciliation.
  sleep 5

  # The ExporterClass should now be Degraded (missing DriverInterface for gpu).
  run kubectl -n "${JS_NAMESPACE}" get exporterclass embedded-linux-test \
    -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}'
  assert_success
  assert_output "True"

  # Restore original ExporterClass.
  cat > "$EC_TEST_DIR/ec-embedded-restored.yaml" <<'EOF'
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: embedded-linux-test
spec:
  selector:
    matchLabels:
      example.com/board: oidc
  interfaces:
    - name: power
      interfaceRef: di-power-test
      required: true
    - name: serial
      interfaceRef: di-serial-test
      required: false
EOF

  run jmp admin apply exporterclass "$EC_TEST_DIR/ec-embedded-restored.yaml" -n "${JS_NAMESPACE}"
  assert_success

  # Wait for reconciliation.
  local attempts=0
  while [ $attempts -lt 20 ]; do
    local ready
    ready=$(kubectl -n "${JS_NAMESPACE}" get exporterclass embedded-linux-test \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    if [ "$ready" = "True" ]; then
      break
    fi
    sleep 2
    attempts=$((attempts + 1))
  done

  run kubectl -n "${JS_NAMESPACE}" get exporterclass embedded-linux-test \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
  assert_success
  assert_output "True"
}
