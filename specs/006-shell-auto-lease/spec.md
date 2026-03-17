# Feature Specification: Shell Auto-Lease

**Feature Branch**: `006-shell-auto-lease`
**Created**: 2026-03-17
**Status**: Draft
**Input**: User description: "Make `jmp shell` (without arguments) auto-connect to existing leases"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Single Active Lease Auto-Connect (Priority: P1)

A user who has exactly one active lease runs `jmp shell` without any flags.
The CLI detects the single lease and automatically connects to it, removing
the need to copy-paste lease names or remember selector labels.

**Why this priority**: This is the most common case for users who create a
lease first and then want to connect. It removes friction from the primary
workflow.

**Independent Test**: Can be tested by mocking `config.list_leases` to return
exactly one active lease and verifying the shell connects using that lease name.

**Acceptance Scenarios**:

1. **Given** a client config with one active lease, **When** the user runs
   `jmp shell` with no selector/lease/name flags, **Then** the CLI auto-connects
   to that lease without prompting.
2. **Given** a client config with one active lease, **When** the user runs
   `jmp shell -- python script.py` with no selector, **Then** the command runs
   against the auto-detected lease.

---

### User Story 2 - No Active Leases Error (Priority: P1)

A user runs `jmp shell` with no arguments and has zero active leases. The CLI
shows a helpful error message guiding them on how to create a lease or use
a selector.

**Why this priority**: Without this, users get a confusing "selector required"
error. This story provides clear guidance, which is equally important to the
happy path.

**Independent Test**: Mock `config.list_leases` to return empty list, verify
error message content.

**Acceptance Scenarios**:

1. **Given** a client config with no active leases, **When** the user runs
   `jmp shell`, **Then** the CLI shows an error with guidance such as
   "No active leases found. Use --selector/-l or --name/-n to create one."

---

### User Story 3 - Multiple Active Leases Interactive Picker (Priority: P2)

A user with multiple active leases runs `jmp shell` without arguments.
The CLI presents an interactive picker allowing them to choose which lease
to connect to.

**Why this priority**: Multi-lease users are a smaller group, and this requires
an interactive terminal. Falls back to an error with a list when not on a TTY.

**Independent Test**: Mock `config.list_leases` to return multiple leases,
verify picker is shown and selection is used.

**Acceptance Scenarios**:

1. **Given** a client config with 3 active leases and a TTY, **When** the user
   runs `jmp shell`, **Then** an interactive picker is shown listing lease names
   and exporters.
2. **Given** a client config with 3 active leases and no TTY (piped), **When**
   the user runs `jmp shell`, **Then** the CLI shows an error listing available
   leases and asks the user to specify with `--lease`.

---

### Edge Cases

- What happens when the lease list API call fails (network error, expired token)?
  The existing exception handling and re-authentication flow should apply.
- What happens when a lease expires between listing and connecting?
  The existing lease connection error handling covers this.
- What happens when `--selector` or `--lease` is explicitly provided alongside auto-detection?
  Auto-detection is skipped; explicit flags always take precedence.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When no `--selector`, `--name`, or `--lease` flag is provided and
  no `JMP_LEASE` env var is set, the CLI MUST query active leases from the server.
- **FR-002**: With exactly one active lease, the CLI MUST auto-connect using
  that lease name without prompting.
- **FR-003**: With zero active leases, the CLI MUST show an error with guidance
  on how to create a lease or use selector flags.
- **FR-004**: With multiple active leases and a TTY, the CLI MUST present an
  interactive picker using `click.prompt` with `click.Choice`.
- **FR-005**: With multiple active leases and no TTY, the CLI MUST show an error
  listing available leases.
- **FR-006**: The interactive picker MUST display lease name and exporter name
  for each option.
- **FR-007**: No new external dependencies MUST be added; the feature MUST use
  `click` (already a dependency) for interactive selection.

### Key Entities

- **Lease**: Existing gRPC model with name, exporter, selector, conditions.
- **LeaseList**: Existing model returned by `config.list_leases()`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `jmp shell` with one active lease connects without extra flags.
- **SC-002**: `jmp shell` with zero leases shows actionable error message.
- **SC-003**: `jmp shell` with multiple leases shows interactive picker on TTY.
- **SC-004**: All existing `jmp shell` flag combinations continue to work unchanged.
- **SC-005**: No new dependencies added to pyproject.toml.
