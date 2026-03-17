# Quickstart: Shell Auto-Lease

**Branch**: `006-shell-auto-lease` | **Date**: 2026-03-17

## Development Setup

```bash
cd python/
uv sync
```

## Running Tests

```bash
make pkg-test-jumpstarter_cli
```

## Implementation Steps

### 1. Write Tests First

Create test cases in the existing test file or a new test file at:
`python/packages/jumpstarter-cli/jumpstarter_cli/shell_test.py`

Test cases needed:
- Zero leases: verify `click.UsageError` is raised with guidance message
- One lease: verify auto-selection returns correct lease name
- Multiple leases + TTY: verify `click.prompt` is called
- Multiple leases + no TTY: verify `click.UsageError` with lease list
- Explicit flags provided: verify auto-detection is skipped

### 2. Implement Selection Function

Add `_select_lease_from_active(config)` to `shell.py`:
- Call `config.list_leases(only_active=True)`
- Branch on lease count (0, 1, N)
- Use `click.prompt` with `click.Choice` for N > 1 on TTY

### 3. Modify Shell Command

Update the `shell` function in `shell.py`:
- Replace the `UsageError` for missing selector/name with a call to
  `_select_lease_from_active`
- Set `lease_name` from the result

### 4. Verify

```bash
make pkg-test-jumpstarter_cli
make lint-fix
make pkg-ty-jumpstarter_cli
```

## Key Files

- `python/packages/jumpstarter-cli/jumpstarter_cli/shell.py` - main changes
- `python/packages/jumpstarter/jumpstarter/config/client.py` - `list_leases` API
- `python/packages/jumpstarter/jumpstarter/client/grpc.py` - `LeaseList`, `Lease` models
