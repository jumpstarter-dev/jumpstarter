# Quickstart: Testing CLI Noun Aliases

**Feature**: 001-noun-aliases
**Date**: 2026-03-17

## Prerequisites

- Python 3.11+
- UV package manager installed
- Repository cloned and on the `001-noun-aliases` branch

## Running Unit Tests

### Alias Resolution Tests

```bash
make pkg-test-jumpstarter_cli_common
```

Or run specific test files:

```bash
cd python/packages/jumpstarter-cli-common
uv run pytest tests/test_alias.py -v
```

### Delete Batch Tests

```bash
make pkg-test-jumpstarter_cli
```

Or run specific test files:

```bash
cd python/packages/jumpstarter-cli
uv run pytest tests/test_delete_batch.py -v
```

## Manual Verification

### Verify Noun Aliases (requires a running jumpstarter controller)

```bash
# These pairs should produce identical output:
jmp get exporters
jmp get exporter

jmp get leases
jmp get lease

# Short aliases should still work:
jmp get e
jmp get l
```

### Verify Batch Delete (requires leases to exist)

```bash
# Create test leases
LEASE1=$(jmp create lease -l app=test --duration 1h --output name)
LEASE2=$(jmp create lease -l app=test --duration 1h --output name)
LEASE3=$(jmp create lease -l app=test --duration 1h --output name)

# Delete all three at once
jmp delete leases "$LEASE1" "$LEASE2" "$LEASE3"
```

## Linting and Type Checking

```bash
make lint-fix
make pkg-ty-jumpstarter_cli_common
make pkg-ty-jumpstarter_cli
```
