#!/usr/bin/env bats
# E2E tests for direct TCP listener mode (no controller)

SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")" && pwd)"
EXPORTER_CONFIG="${SCRIPT_DIR}/exporters/exporter-direct-listener.yaml"
LISTENER_PORT=19090
LISTENER_PID=""

setup() {
  bats_load_library bats-support
  bats_load_library bats-assert

  bats_require_minimum_version 1.5.0
}

# Start the exporter in the background.
#   $1 - config file (default: $EXPORTER_CONFIG)
#   $2 - readiness: "grpc" waits via jmp shell (drains LogStream),
#                   "port" waits via nc -z (preserves LogStream queue)
#   $3 - if set, redirect stderr to ${BATS_TEST_TMPDIR}/exporter.log
#   $4 - passphrase (optional)
_start_exporter() {
  local config="${1:-$EXPORTER_CONFIG}"
  local readiness="${2:-grpc}"
  local capture_logs="${3:-}"
  local passphrase="${4:-}"

  local extra_args=()
  if [ -n "$passphrase" ]; then
    extra_args+=(--passphrase "$passphrase")
  fi

  if [ -n "$capture_logs" ]; then
    jmp run --exporter-config "$config" \
      --tls-grpc-listener "$LISTENER_PORT" \
      --tls-grpc-insecure "${extra_args[@]}" 2>"${BATS_TEST_TMPDIR}/exporter.log" &
  else
    jmp run --exporter-config "$config" \
      --tls-grpc-listener "$LISTENER_PORT" \
      --tls-grpc-insecure "${extra_args[@]}" &
  fi
  LISTENER_PID=$!
  echo "$LISTENER_PID" > "${BATS_TEST_TMPDIR}/exporter.pid"

  local retries=30
  if [ "$readiness" = "port" ]; then
    # TCP-only check: doesn't drain the LogStream queue, so hook output
    # remains buffered for the test command to consume.
    while ! nc -z 127.0.0.1 "$LISTENER_PORT" 2>/dev/null; do
      retries=$((retries - 1))
      if [ "$retries" -le 0 ]; then
        echo "Port $LISTENER_PORT did not become available" >&2
        return 1
      fi
      sleep 0.5
    done
  else
    # Full gRPC check: ensures exporter is ready for commands.
    # Drains LogStream queue (unsuitable for hook output tests).
    local grpc_args=(--tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure)
    if [ -n "$passphrase" ]; then
      grpc_args+=(--passphrase "$passphrase")
    fi
    while ! jmp shell "${grpc_args[@]}" -- j --help >/dev/null 2>&1; do
      retries=$((retries - 1))
      if [ "$retries" -le 0 ]; then
        echo "Exporter did not become ready in time" >&2
        return 1
      fi
      sleep 0.5
    done
  fi
}

start_exporter()              { _start_exporter "$1" grpc; }
start_exporter_with_logs()    { _start_exporter "$1" grpc logs; }
start_exporter_bg()           { _start_exporter "$1" port; }
start_exporter_bg_with_logs() { _start_exporter "$1" port logs; }

start_exporter_with_passphrase() { _start_exporter "${2:-$EXPORTER_CONFIG}" grpc "" "$1"; }

stop_exporter() {
  if [ -f "${BATS_TEST_TMPDIR}/exporter.pid" ]; then
    local pid
    pid=$(cat "${BATS_TEST_TMPDIR}/exporter.pid")
    if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
    rm -f "${BATS_TEST_TMPDIR}/exporter.pid"
  fi
}

teardown() {
  stop_exporter
}

@test "direct listener: exporter starts and client can connect" {
  start_exporter

  run jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure -- j power on
  assert_success
}

@test "direct listener: client can call multiple driver methods" {
  start_exporter

  run jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure -- j power on
  assert_success

  run jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure -- j power off
  assert_success
}

@test "direct listener: client without --tls-grpc-insecure fails against insecure server" {
  start_exporter

  run jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" -- j power on
  assert_failure
}

@test "direct listener hooks: beforeLease hook executes and j commands work" {
  # Use start_exporter_bg (TCP-only readiness check) to avoid draining
  # the LogStream queue before the test command connects.
  start_exporter_bg "${SCRIPT_DIR}/exporters/exporter-direct-hooks-before.yaml"

  run jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure \
    --exporter-logs -- j power off
  assert_success
  assert_output --partial "BEFORE_HOOK_DIRECT: executed"
  assert_output --partial "BEFORE_HOOK_DIRECT: complete"
}

@test "direct listener hooks: afterLease hook runs on exporter shutdown" {
  start_exporter_bg_with_logs "${SCRIPT_DIR}/exporters/exporter-direct-hooks-both.yaml"

  run jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure \
    --exporter-logs -- j power on
  assert_success
  assert_output --partial "BEFORE_HOOK_DIRECT: executed"

  # Stop the exporter (SIGTERM triggers _cleanup_after_lease).
  # stop_exporter waits for the process to exit, so the log is complete.
  stop_exporter

  # afterLease hook output should appear in the exporter's stderr log
  run cat "${BATS_TEST_TMPDIR}/exporter.log"
  assert_output --partial "AFTER_HOOK_DIRECT: executed"
}

@test "direct listener passphrase: correct passphrase connects" {
  start_exporter_with_passphrase "my-secret"

  run jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure \
    --passphrase "my-secret" -- j power on
  assert_success
}

@test "direct listener passphrase: wrong passphrase is rejected" {
  start_exporter_with_passphrase "my-secret"

  run jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure \
    --passphrase "wrong" -- j power on
  assert_failure
}

@test "direct listener passphrase: missing passphrase is rejected" {
  start_exporter_with_passphrase "my-secret"

  run jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure \
    -- j power on
  assert_failure
}
