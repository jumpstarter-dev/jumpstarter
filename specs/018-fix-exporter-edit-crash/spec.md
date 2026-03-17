# Feature Specification: Fix Exporter Edit Crash

**Feature Branch**: `018-fix-exporter-edit-crash`
**Created**: 2026-03-17
**Status**: Draft
**Input**: User description: "jmp config exporter edit crashes with TypeError: PosixPath not iterable - issue #251"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Edit exporter config without crash (Priority: P1)

A user runs `jmp config exporter edit <name>` to edit their exporter configuration in their default editor. Currently, the command crashes with `TypeError: 'PosixPath' object is not iterable` because the config's `path` field (a `pathlib.Path` object) is passed directly to `click.edit()` which expects a string.

**Why this priority**: This is a crash bug that completely blocks the edit workflow.

**Independent Test**: Run `jmp config exporter edit <name>` with a valid exporter config and verify the editor opens without crashing.

**Acceptance Scenarios**:

1. **Given** a valid exporter config exists, **When** user runs `jmp config exporter edit <name>`, **Then** the default editor opens with the config file.
2. **Given** no exporter config exists for the given name, **When** user runs `jmp config exporter edit nonexistent`, **Then** a clear error message is shown (not a crash).

---

### Edge Cases

- What if the config file exists but is not readable?
- What if the EDITOR environment variable is not set?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The `jmp config exporter edit` command MUST convert the config path to a string before passing it to the editor.
- **FR-002**: The command MUST NOT crash with a TypeError for any valid exporter config.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `jmp config exporter edit <name>` opens the editor without crashing.
- **SC-002**: The fix is covered by a unit test that verifies a string path is passed to the editor function.
