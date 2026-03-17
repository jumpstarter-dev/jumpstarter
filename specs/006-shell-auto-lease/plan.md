# Implementation Plan: Shell Auto-Lease

**Branch**: `006-shell-auto-lease` | **Date**: 2026-03-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/006-shell-auto-lease/spec.md`

## Summary

When `jmp shell` is invoked without `--selector`, `--name`, `--lease`, or
`$JMP_LEASE`, the CLI queries the server for active leases and auto-connects.
With one lease it connects directly; with zero it shows an actionable error;
with multiple it presents an interactive `click.prompt` picker (or an error
listing on non-TTY). No new dependencies are needed -- the feature uses the
existing `click` and `config.list_leases()` APIs.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click (>=8.1.7.2), gRPC, anyio, rich (>=14.0.0)
**Storage**: N/A (leases stored server-side, queried via gRPC)
**Testing**: pytest, Click CliRunner, unittest.mock
**Target Platform**: Linux, macOS
**Project Type**: CLI tool (monorepo package)
**Performance Goals**: N/A (single gRPC call added to startup path)
**Constraints**: No new external dependencies
**Scale/Scope**: Single file change (~60 lines of new code + tests)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Clean Code | PASS | Small function with single responsibility |
| II. Minimal Dependencies | PASS | Uses existing click, no new deps |
| III. Secure Coding | PASS | No new external input surfaces |
| IV. TDD | PASS | Tests written before implementation |
| V. Simplicity | PASS | click.prompt is simplest viable approach |

## Project Structure

### Documentation (this feature)

```text
specs/006-shell-auto-lease/
  plan.md              # This file
  spec.md              # Feature specification
  research.md          # Phase 0: dependency and API research
  data-model.md        # Phase 1: lease selection flow
  quickstart.md        # Phase 1: development quickstart
  contracts/
    cli-shell.md       # Phase 1: updated shell command behavior
```

### Source Code (repository root)

```text
python/packages/jumpstarter-cli/jumpstarter_cli/
  shell.py             # Modified: add _select_lease_from_active(), update shell()
  shell_test.py        # New: unit tests for auto-lease selection logic
```

**Structure Decision**: Changes are confined to the `jumpstarter-cli` package.
Only `shell.py` is modified; a test file is added alongside it following the
existing `*_test.py` naming convention used throughout the project (e.g.,
`get_test.py`, `lease_test.py`).

## Complexity Tracking

No constitution violations. No complexity justifications needed.
