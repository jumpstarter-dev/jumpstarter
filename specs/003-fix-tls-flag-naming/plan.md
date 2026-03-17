# Implementation Plan: Fix TLS Flag Naming

**Branch**: `003-fix-tls-flag-naming` | **Date**: 2026-03-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-fix-tls-flag-naming/spec.md`

## Summary

Rename the inconsistent `--insecure-tls-config` CLI flag to `--insecure-tls`
across all commands (`jmp login`, `jmp admin create`, `jmp admin import`). The
old name is preserved as a hidden deprecated alias that emits a warning to
stderr. The two login-specific flags (`--insecure-login-tls`,
`--insecure-login-http`) are already well-named and remain unchanged.

The implementation uses Click's `hidden=True` option with a shared destination
parameter to provide backward-compatible aliasing with zero custom subclasses.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click (CLI framework)
**Storage**: N/A (flag rename only, no data changes)
**Testing**: pytest (unit tests in `*_test.py` files), bats (e2e tests)
**Target Platform**: Linux, macOS (CLI tool)
**Project Type**: CLI
**Performance Goals**: N/A (no runtime impact)
**Constraints**: Backward compatibility required for at least one minor release
**Scale/Scope**: 9 files modified across 4 packages + docs + e2e

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Clean Code | PASS | Renaming improves intention-revealing naming |
| II. Minimal Dependencies | PASS | No new dependencies; uses existing Click features |
| III. Secure Coding | PASS | Security-relevant flags retain clear warnings |
| IV. Test-Driven Development | PASS | Tests will be written for new flag name and deprecation warning before implementation |
| V. Simplicity | PASS | Dual-option with hidden=True is the simplest deprecation approach |
| Security Requirements | PASS | TLS flags remain functional; no security regression |
| Development Workflow | PASS | Single concern (flag rename), conventional commits |

No violations. No complexity tracking needed.

## Project Structure

### Documentation (this feature)

```text
specs/003-fix-tls-flag-naming/
+-- plan.md              # This file
+-- research.md          # Click deprecation pattern research
+-- data-model.md        # Flag mapping (old -> new)
+-- quickstart.md        # Migration guide
+-- contracts/
|   +-- cli-flags.md     # Flag names and deprecation behavior contract
+-- tasks.md             # Task breakdown (created separately)
```

### Source Code (repository root)

```text
python/packages/jumpstarter-cli-common/jumpstarter_cli_common/
+-- opt.py                    # Flag definitions (primary change)

python/packages/jumpstarter-cli/jumpstarter_cli/
+-- login.py                  # Update import and parameter name

python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/
+-- create.py                 # Update import and parameter name
+-- create_test.py            # Add new-flag tests, keep old-flag tests
+-- import_res.py             # Update import and parameter name
+-- import_res_test.py        # Add new-flag tests, keep old-flag tests

python/docs/source/getting-started/
+-- guides/setup-distributed-mode.md    # Update flag references
+-- configuration/authentication.md     # Update flag references

e2e/
+-- tests.bats                # Update flag references
```

**Structure Decision**: This is a cross-cutting rename across an existing
monorepo. No new packages or directories are needed. Changes touch the shared
`jumpstarter-cli-common` package (where the option is defined), the two CLI
packages that consume it, documentation, and e2e tests.

## Complexity Tracking

No constitution violations. Table intentionally left empty.
