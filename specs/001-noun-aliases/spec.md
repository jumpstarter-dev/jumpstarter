# Feature Specification: CLI Noun Aliases

**Feature Branch**: `001-noun-aliases`
**Created**: 2026-03-17
**Status**: Draft
**Input**: User description: "Make CLI noun subcommands accept both singular and plural forms and support batch delete with multiple IDs"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Singular/Plural Noun Interchangeability (Priority: P1)

A user should be able to type either the singular or plural form of any noun subcommand and get the same result. For example, `jmp get exporter` and `jmp get exporters` should both work, as should `jmp delete lease` and `jmp delete leases`.

**Why this priority**: This is the core value proposition -- reducing friction for users who cannot remember which form the CLI expects.

**Independent Test**: Can be fully tested by invoking CLI commands with both singular and plural noun forms and verifying identical behavior.

**Acceptance Scenarios**:

1. **Given** the CLI is installed, **When** user runs `jmp get exporter`, **Then** the output is identical to `jmp get exporters`
2. **Given** the CLI is installed, **When** user runs `jmp get lease`, **Then** the output is identical to `jmp get leases`
3. **Given** the CLI is installed, **When** user runs `jmp create lease`, **Then** the command works (already singular, plural `leases` should also work)
4. **Given** the CLI is installed, **When** user runs `jmp delete lease <name>`, **Then** the command works identically to `jmp delete leases <name>`
5. **Given** the CLI is installed, **When** user runs `jmp update lease <name>`, **Then** the command works (already singular, plural `leases` should also work)

---

### User Story 2 - Batch Delete with Multiple IDs (Priority: P2)

A user should be able to pass multiple resource names to `jmp delete leases` in a single invocation rather than running the command once per resource.

**Why this priority**: Reduces repetitive command invocations in scripting and interactive use.

**Independent Test**: Can be tested by creating multiple leases and deleting them in one `jmp delete leases <id1> <id2> <id3>` invocation.

**Acceptance Scenarios**:

1. **Given** three leases exist, **When** user runs `jmp delete leases lease1 lease2 lease3`, **Then** all three leases are deleted
2. **Given** three leases exist, **When** user runs `jmp delete leases lease1 lease2 --output name`, **Then** both names are printed, one per line
3. **Given** no names, no selector, and no --all flag, **When** user runs `jmp delete leases`, **Then** an error is raised as before

---

### User Story 3 - Admin CLI Noun Aliases (Priority: P3)

The admin CLI (`jmp admin`) should also accept singular/plural interchangeably for its noun subcommands (client/clients, exporter/exporters, cluster/clusters).

**Why this priority**: Consistency across all CLI surfaces.

**Independent Test**: Can be tested by invoking admin subcommands with alternate noun forms.

**Acceptance Scenarios**:

1. **Given** admin CLI is available, **When** user runs `jmp admin get clients`, **Then** it works the same as `jmp admin get client`
2. **Given** admin CLI is available, **When** user runs `jmp admin delete exporters <name>`, **Then** it works the same as `jmp admin delete exporter <name>`

---

### Edge Cases

- What happens when a user passes zero names to batch delete without --selector or --all? Error as before.
- What happens when one name in a batch delete fails? The command should continue deleting remaining names and report failures.
- Are there any noun collisions where singular and plural map to different commands? Yes: in admin `get`, `cluster` (get one by name) and `clusters` (list all) are separate commands -- these must NOT be aliased to each other.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The `AliasedGroup.common_aliases` dictionary MUST include both singular and plural forms for all noun subcommands: exporter/exporters, lease/leases, client/clients, cluster/clusters
- **FR-002**: The user CLI `delete leases` command MUST accept multiple NAME arguments via `nargs=-1`
- **FR-003**: All existing single-letter and short aliases MUST be preserved
- **FR-004**: Admin CLI commands where singular and plural are distinct commands (e.g., `get cluster` vs `get clusters`) MUST NOT be aliased to each other
- **FR-005**: The existing `--selector` and `--all` flags on delete MUST continue to work alongside batch names

### Key Entities

- **AliasedGroup**: Click group subclass that resolves command aliases from `common_aliases` dict
- **common_aliases**: Dictionary mapping canonical command names to lists of accepted aliases

## Clarifications

No critical ambiguities were found. The following observations were noted:

1. The `common_aliases` dict already contains `exporter -> [exporters, e]`, `client -> [clients, c]`, and `lease -> [leases, l]`. These cover noun aliases for commands registered under their singular canonical name.
2. For commands registered under plural names (e.g., `get exporters`, `get leases`, `delete leases`), the reverse alias (`exporters -> [exporter]`) is needed so users can type the singular form.
3. The admin CLI has `cluster` and `clusters` as separate commands under `get` -- these must remain distinct.
4. `cluster` alias is missing from `common_aliases` entirely and should be added as `cluster -> [clusters]` for admin create/delete contexts (but not for admin get, where they differ).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All noun subcommands accept both singular and plural forms (verified by unit tests)
- **SC-002**: `jmp delete leases name1 name2 name3` deletes all specified leases (verified by unit test)
- **SC-003**: All existing CLI tests continue to pass
- **SC-004**: No new external dependencies are introduced
