# Feature Specification: Short Flag Aliases

**Feature Branch**: `008-short-flag-aliases`
**Created**: 2026-03-17
**Status**: Draft
**Input**: Add `-a` as a short alias for `--all` and audit other commonly used flags for missing short aliases.

## User Scenarios & Testing

### User Story 1 - Short alias for --all on get leases (Priority: P1)

A CLI user running `jmp get leases` wants to quickly include expired leases
without typing `--all`. Using `-a` is faster and consistent with common CLI
conventions (e.g., `ls -a`).

**Why this priority**: The `--all` flag on `get leases` is the explicitly
requested change and the most commonly used flag that lacks a short alias
in the main CLI.

**Independent Test**: Can be tested by invoking `jmp get leases -a` and
verifying it behaves identically to `jmp get leases --all`.

**Acceptance Scenarios**:

1. **Given** a user has active and expired leases, **When** they run
   `jmp get leases -a`, **Then** both active and expired leases are displayed.
2. **Given** a user runs `jmp get leases --all`, **When** they compare output
   to `jmp get leases -a`, **Then** the outputs are identical.

---

### User Story 2 - Short alias for --all on delete leases (Priority: P1)

A CLI user running `jmp delete leases --all` wants to use `-a` as a shorthand.
However, `-a` is NOT available here because `delete leases` also inherits
`-l` (selector) from `opt_selector`, and no other short flags conflict.
Note: The `delete leases` command does not use `--allow`/`-a`, so `-a` is
available for `--all` in this context.

**Why this priority**: Consistency with `get leases -a`.

**Independent Test**: Run `jmp delete leases -a` and confirm it deletes
all owned leases.

**Acceptance Scenarios**:

1. **Given** a user has leases, **When** they run `jmp delete leases -a`,
   **Then** all their owned leases are deleted.

---

### User Story 3 - Audit and add short aliases for other common flags (Priority: P2)

Audit all CLI commands for long flags that lack short aliases and would
benefit from them. Add short aliases where they do not conflict with
existing flags on the same command.

**Why this priority**: Improves overall CLI ergonomics but is less critical
than the explicitly requested `--all` alias.

**Independent Test**: Each new alias can be tested individually by running
the command with the short flag and verifying identical behavior to the
long flag.

**Acceptance Scenarios**:

1. **Given** a flag `--verbose` on `auth status`, **When** the user runs
   `jmp auth status -v`, **Then** verbose output is shown.

---

### Edge Cases

- What happens when `-a` is used on a command where it already maps to
  `--allow` (e.g., `config client create`)? Answer: No change; `-a`
  already means `--allow` there. The alias is per-command, not global.
- What happens when a user passes both `-a` and `--all`? Answer: Click
  treats them as the same option; no conflict.

## Requirements

### Functional Requirements

- **FR-001**: The `get leases` command MUST accept `-a` as a short alias
  for `--all`.
- **FR-002**: The `delete leases` command MUST accept `-a` as a short alias
  for `--all`.
- **FR-003**: The `auth status` command MUST accept `-v` as a short alias
  for `--verbose`.
- **FR-004**: No existing short alias mappings MUST be changed or broken.
- **FR-005**: Help text (`--help`) MUST display both short and long forms
  for all modified flags.

### Key Entities

- **Click Option**: A CLI flag definition that may include a short alias
  (single dash + letter) and a long form (double dash + word).

## Success Criteria

### Measurable Outcomes

- **SC-001**: All commands with `--all` accept `-a` where no conflict
  exists.
- **SC-002**: All new short aliases are documented in `--help` output.
- **SC-003**: Existing tests continue to pass with no regressions.
- **SC-004**: New tests verify that each short alias produces the same
  behavior as its long form.
