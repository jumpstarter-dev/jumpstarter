# Implementation Plan: Fix Driver List

**Branch**: `007-fix-driver-list` | **Date**: 2026-03-17 | **Spec**: Bug fix -- no separate spec
**Input**: `jmp driver list` does not show all installed drivers

## Summary

The `jmp driver list` command discovers drivers via `entry_points(group="jumpstarter.drivers")`.
16 driver packages are missing the `[project.entry-points."jumpstarter.drivers"]` section in
their `pyproject.toml`, making them invisible to the command. The fix adds the missing
entry-points to each affected package's `pyproject.toml`.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: setuptools entry-points, importlib.metadata, hatchling build system
**Storage**: N/A
**Testing**: pytest, `make pkg-test-<package_name>`
**Target Platform**: Linux, macOS
**Project Type**: CLI tool / monorepo with driver packages
**Performance Goals**: N/A (metadata-only change)
**Constraints**: Each entry-point name must match the class name; module path must be correct
**Scale/Scope**: 15 pyproject.toml files to modify (16 missing minus 1 excluded)

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Clean Code | PASS | Configuration-only change; no code added |
| II. Minimal Dependencies | PASS | No new dependencies |
| III. Secure Coding | PASS | No security impact |
| IV. Test-Driven Development | PASS | Write a test that verifies entry-points are present before making changes |
| V. Simplicity | PASS | Minimal change -- adding TOML metadata only |

## Project Structure

### Documentation (this feature)

```text
specs/007-fix-driver-list/
  plan.md              # This file
  research.md          # Audit of all driver packages
  data-model.md        # Entry-point registration mapping
  quickstart.md        # Verification steps
```

### Source Code (repository root)

Changes are limited to `pyproject.toml` in 15 driver packages:

```text
python/packages/
  jumpstarter-driver-ble/pyproject.toml
  jumpstarter-driver-flashers/pyproject.toml
  jumpstarter-driver-http/pyproject.toml
  jumpstarter-driver-http-power/pyproject.toml
  jumpstarter-driver-iscsi/pyproject.toml
  jumpstarter-driver-probe-rs/pyproject.toml
  jumpstarter-driver-pyserial/pyproject.toml
  jumpstarter-driver-qemu/pyproject.toml
  jumpstarter-driver-ridesx/pyproject.toml
  jumpstarter-driver-snmp/pyproject.toml
  jumpstarter-driver-ssh/pyproject.toml
  jumpstarter-driver-tftp/pyproject.toml
  jumpstarter-driver-tmt/pyproject.toml
  jumpstarter-driver-uboot/pyproject.toml
  jumpstarter-driver-ustreamer/pyproject.toml
```

**Structure Decision**: No structural changes. Only metadata additions to existing files.

## Complexity Tracking

No violations. This is a straightforward configuration fix.
