# Implementation Plan: Shell Completion

**Branch**: `004-shell-completion` | **Date**: 2026-03-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/004-shell-completion/spec.md`

## Summary

Add a `jmp completion [bash|zsh|fish]` subcommand that generates shell-specific completion scripts using Click's built-in `click.shell_completion` module. No additional dependencies are required since Click already provides completion support for all three target shells. The command will accept a shell name as a required argument, use `get_completion_class()` to obtain the appropriate completion class, and print the generated script to stdout.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click (already a dependency, provides `click.shell_completion`)
**Storage**: N/A (no persistence, output goes to stdout)
**Testing**: pytest, Click's `CliRunner` for CLI testing
**Target Platform**: Linux (primary), macOS (supported)
**Project Type**: CLI tool (part of `jumpstarter-cli` package)
**Performance Goals**: N/A (one-shot script generation)
**Constraints**: Must use Click's built-in completion; no external dependencies
**Scale/Scope**: Single new command module with tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **Clean Code**: Single-responsibility command module; self-explanatory code
- [x] **Minimal Dependencies**: Uses only Click's built-in `shell_completion` module; zero new dependencies
- [x] **Secure Coding**: No user input beyond shell name (validated by Click Choice); no secrets, no network
- [x] **Test-Driven Development**: Tests will be written first using Click's CliRunner
- [x] **Simplicity**: Minimal implementation; no abstractions beyond what Click provides

## Project Structure

### Documentation (this feature)

```text
specs/004-shell-completion/
+-- plan.md              # This file
+-- spec.md              # Feature specification
+-- research.md          # Click shell_completion research
+-- data-model.md        # Supported shells model
+-- contracts/
|   +-- cli-completion.md # Completion subcommand interface
+-- quickstart.md        # Installation and testing guide
+-- tasks.md             # Task breakdown (created by /speckit.tasks)
```

### Source Code (repository root)

```text
python/packages/jumpstarter-cli/jumpstarter_cli/
+-- completion.py        # NEW: completion command implementation
+-- completion_test.py   # NEW: completion command tests
+-- jmp.py               # MODIFIED: register completion command
```

**Structure Decision**: This feature adds a single new command module (`completion.py`) to the existing `jumpstarter-cli` package, following the same pattern as other commands (`version.py`, `shell.py`, etc.). No new packages or structural changes are needed.

## Complexity Tracking

No constitution violations. The implementation is minimal:
- One new file with a single Click command
- One new test file
- One import and one `add_command` line in `jmp.py`
