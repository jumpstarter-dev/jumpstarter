# Implementation Plan: StorageMux Auto-Detect Compression

**Branch**: `014-storagemux-auto-compression` | **Date**: 2026-03-17 | **Spec**: [spec.md](spec.md)

## Summary

Add compression auto-detection to StorageMux drivers (SDWire, SDMux, DUTLink) based on file extension, matching the existing Flasher driver behavior. Extract the shared detection logic into a common utility.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click (CLI), jumpstarter driver framework
**Storage**: N/A
**Testing**: pytest
**Target Platform**: Linux
**Project Type**: CLI tool / driver
**Performance Goals**: N/A
**Constraints**: Must match existing Flasher driver behavior exactly
**Scale/Scope**: Common utility + StorageMux driver integration

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Clean Code | Pass | Extract shared logic to avoid duplication |
| II. Minimal Dependencies | Pass | No new deps |
| III. Secure Coding | Pass | No security surface |
| IV. TDD | Pass | Test extension detection logic |
| V. Simplicity | Pass | Reuse existing pattern from Flasher |

## Project Structure

### Source Code (repository root)

```text
python/packages/jumpstarter-driver-flashers/
└── jumpstarter_driver_flashers/   # Existing Flasher compression detection
python/packages/jumpstarter-driver-sdwire/
└── jumpstarter_driver_sdwire/     # Add auto-detection
python/packages/jumpstarter-driver-sdmux/
└── jumpstarter_driver_sdmux/      # Add auto-detection (if separate)
```

**Structure Decision**: Extract compression detection to a shared utility, use it in both Flasher and StorageMux drivers.
