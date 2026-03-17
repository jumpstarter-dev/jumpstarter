# Implementation Plan: Fix Exporter Edit Crash

**Branch**: `018-fix-exporter-edit-crash` | **Date**: 2026-03-17 | **Spec**: [spec.md](spec.md)

## Summary

Fix `jmp config exporter edit` crash caused by passing a `PosixPath` to `click.edit()` which expects a string. Cast `config.path` to `str()` before the call.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click (CLI), pathlib
**Storage**: N/A
**Testing**: pytest
**Target Platform**: Linux, macOS
**Project Type**: CLI tool
**Performance Goals**: N/A
**Constraints**: One-line fix
**Scale/Scope**: Single file change in `config_exporter.py`

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Clean Code | Pass | Type correction |
| II. Minimal Dependencies | Pass | No new deps |
| III. Secure Coding | Pass | No security impact |
| IV. TDD | Pass | Test that string path is passed to click.edit |
| V. Simplicity | Pass | One-line fix |

## Project Structure

### Source Code (repository root)

```text
python/packages/jumpstarter-cli/jumpstarter_cli/
├── config_exporter.py       # Cast config.path to str()
└── config_exporter_test.py  # Verify click.edit receives string
```

**Structure Decision**: Fix in existing file, add test file.
