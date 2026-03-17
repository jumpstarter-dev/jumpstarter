# Research: Shell Auto-Lease

**Branch**: `006-shell-auto-lease` | **Date**: 2026-03-17

## Interactive Picker Options

### Option 1: click.prompt with click.Choice (SELECTED)

Click provides `click.prompt(type=click.Choice(...))` which renders a numbered
list and accepts user input. This is already available via `click>=8.1.7.2` in
`jumpstarter-cli-common`.

Pros:
- Zero new dependencies
- Consistent with existing CLI framework
- Works with Click's existing error handling
- Simple implementation

Cons:
- Not as visually rich as arrow-key navigation (beaupy/questionary)
- Uses numbered selection rather than cursor-based picking

### Option 2: beaupy

`beaupy` is a third-party library for interactive terminal menus with
arrow-key navigation. It is NOT currently a dependency of any package in the
project.

Pros:
- Visually appealing arrow-key selection

Cons:
- New dependency (violates Principle II: Minimal Dependencies)
- Pulls in additional transitive dependencies
- Not widely adopted compared to alternatives
- Would need to be added to pyproject.toml

### Option 3: rich.prompt

`rich>=14.0.0` is already a dependency via `jumpstarter-cli-common`. It provides
`rich.prompt.Prompt` with basic input handling but no multi-choice picker.

Not suitable for interactive selection on its own.

### Decision

Use `click.prompt` with `click.Choice` for interactive selection. This aligns
with the constitution's Principle II (Minimal Dependencies) by using the
existing Click dependency rather than adding beaupy or any other library.

## Lease Listing via gRPC

The existing `config.list_leases()` method on `ClientConfigV1Alpha1`:
- Calls `ClientService.ListLeases()` via gRPC
- Returns a `LeaseList` with a `leases` attribute (list of `Lease` objects)
- Supports `only_active=True` to filter expired leases
- Supports `filter` parameter for selector-based filtering
- Each `Lease` has: `name`, `exporter`, `selector`, `conditions`, `namespace`,
  `effective_begin_time`, `effective_end_time`

The `list_leases` method is synchronous (decorated with `@_blocking_compat`),
so it can be called directly from the Click command function before entering
the async shell flow.

## TTY Detection

Python's `sys.stdin.isatty()` provides reliable TTY detection. Click also
provides this via the context. This will be used to decide between interactive
picker and error-with-list for the multiple-leases case.

## Existing Shell Command Flow

The `shell` command in `shell.py`:
1. Receives Click options: `config`, `command`, `lease_name`, `selector`,
   `exporter_name`, `duration`, `exporter_logs`, `acquisition_timeout`
2. For `ClientConfigV1Alpha1`: checks if selector/name/lease is provided
3. Calls `anyio.run(_shell_with_signal_handling, ...)` which uses
   `config.lease_async()` to acquire/reuse a lease

The auto-lease logic should be inserted at step 2, before the existing
validation that requires selector/name. When no flags are provided, we:
1. Call `config.list_leases(only_active=True)`
2. Based on count: auto-select, error, or prompt
3. Set `lease_name` to the selected lease's name
4. Continue with existing flow
