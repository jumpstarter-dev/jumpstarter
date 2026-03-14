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

start_exporter() {
  jmp run --exporter-config "$EXPORTER_CONFIG" \
    --tls-grpc-listener "$LISTENER_PORT" \
    --tls-grpc-insecure &
  LISTENER_PID=$!
  echo "$LISTENER_PID" > "${BATS_TEST_TMPDIR}/exporter.pid"

  # Wait for the gRPC server to be ready
  local retries=30
  while ! jmp shell --tls-grpc "127.0.0.1:${LISTENER_PORT}" --tls-grpc-insecure -- j --help >/dev/null 2>&1; do
    retries=$((retries - 1))
    if [ "$retries" -le 0 ]; then
      echo "Exporter did not become ready in time" >&2
      return 1
    fi
    sleep 0.5
  done
}

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
