# Implementation Plan: CLI Noun Aliases

**Branch**: `001-noun-aliases` | **Date**: 2026-03-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-noun-aliases/spec.md`

## Summary

Add singular/plural noun aliases across all CLI CRUD commands so users can type either form interchangeably (e.g., `jmp get exporter` = `jmp get exporters`). Additionally, support batch delete by accepting multiple NAME arguments in `jmp delete leases`. The implementation extends the existing `AliasedGroup.common_aliases` dictionary and modifies the delete command's Click argument to accept variadic names.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click (CLI framework), pytest (testing)
**Storage**: N/A
**Testing**: pytest, Click's CliRunner for CLI integration tests
**Target Platform**: Linux, macOS
**Project Type**: CLI tool (monorepo with multiple packages)
**Performance Goals**: N/A (CLI startup time only)
**Constraints**: No new external dependencies
**Scale/Scope**: Changes to 2 packages (jumpstarter-cli-common, jumpstarter-cli)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Clean Code | PASS | Changes are small, intention-revealing, single-responsibility |
| II. Minimal Dependencies | PASS | No new dependencies; uses existing Click features |
| III. Secure Coding | PASS | No security-sensitive changes; input validation via Click |
| IV. Test-Driven Development | PASS | All changes will be driven by failing tests first |
| V. Simplicity | PASS | Extends existing alias mechanism rather than introducing new abstractions |

## Project Structure

### Documentation (this feature)

```text
specs/001-noun-aliases/
+-- plan.md              # This file
+-- research.md          # AliasedGroup analysis and alias mapping research
+-- data-model.md        # Alias mapping data structure documentation
+-- quickstart.md        # How to test the feature
+-- contracts/
|   +-- cli-nouns.md     # Noun alias contract
+-- tasks.md             # Task breakdown (created separately)
```

### Source Code (repository root)

```text
python/packages/jumpstarter-cli-common/
+-- jumpstarter_cli_common/
    +-- alias.py                    # MODIFY: extend common_aliases dict

python/packages/jumpstarter-cli/
+-- jumpstarter_cli/
    +-- delete.py                   # MODIFY: change NAME to nargs=-1 for batch delete
+-- tests/
    +-- test_alias.py               # ADD: unit tests for noun alias resolution
    +-- test_delete_batch.py        # ADD: unit tests for batch delete
```

**Structure Decision**: Monorepo with UV workspace. Changes span two packages: `jumpstarter-cli-common` (shared alias infrastructure) and `jumpstarter-cli` (delete command modification). Tests live alongside their respective packages.

## Complexity Tracking

No constitution violations. No complexity justifications needed.
