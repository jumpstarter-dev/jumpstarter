# Tasks: Shell Auto-Lease (006)

## T001 [US1] Write failing test for single-lease auto-connect
- **Status**: completed
- **Description**: Add a test that verifies when `jmp shell` is invoked with no selector, no name, and no lease, but exactly one active lease exists, the shell auto-connects using that lease's name.
- **File**: `python/packages/jumpstarter-cli/jumpstarter_cli/shell_test.py`

## T002 [US1] Implement single-lease auto-connect in shell.py
- **Status**: completed
- **Description**: When no selector/name/lease is provided, call `config.list_leases(only_active=True)` and if exactly one lease exists, set `lease_name` to that lease's name and proceed.
- **File**: `python/packages/jumpstarter-cli/jumpstarter_cli/shell.py`

## T003 [US2] Write failing test for no-leases error message
- **Status**: completed
- **Description**: Add a test that verifies when no active leases exist and no selector/name/lease is provided, a `UsageError` is raised with guidance text.
- **File**: `python/packages/jumpstarter-cli/jumpstarter_cli/shell_test.py`

## T004 [US2] Implement no-leases error handling
- **Status**: completed
- **Description**: When `list_leases` returns zero leases, raise `UsageError` with a helpful message explaining how to create a lease or specify a selector.
- **File**: `python/packages/jumpstarter-cli/jumpstarter_cli/shell.py`

## T005 [US3] Write failing test for multi-lease picker (mock TTY)
- **Status**: completed
- **Description**: Add a test that verifies when multiple active leases exist and stdin is a TTY, an interactive picker (`click.prompt` with `click.Choice`) is presented and the selected lease is used.
- **File**: `python/packages/jumpstarter-cli/jumpstarter_cli/shell_test.py`

## T006 [US3] Implement multi-lease interactive picker
- **Status**: completed
- **Description**: When multiple leases exist and stdin is a TTY, use `click.prompt` with `click.Choice` to let the user pick a lease.
- **File**: `python/packages/jumpstarter-cli/jumpstarter_cli/shell.py`

## T007 [US3] Write failing test for multi-lease non-TTY error
- **Status**: completed
- **Description**: Add a test that verifies when multiple active leases exist and stdin is NOT a TTY, a `UsageError` is raised listing available lease names.
- **File**: `python/packages/jumpstarter-cli/jumpstarter_cli/shell_test.py`

## T008 [US3] Implement non-TTY fallback error
- **Status**: completed
- **Description**: When multiple leases exist and stdin is not a TTY, raise `UsageError` listing available leases.
- **File**: `python/packages/jumpstarter-cli/jumpstarter_cli/shell.py`

## T009 Run all tests and linting
- **Status**: completed
- **Description**: Run `make pkg-test-jumpstarter_cli` and `make lint-fix` to ensure all tests pass and code is clean.
