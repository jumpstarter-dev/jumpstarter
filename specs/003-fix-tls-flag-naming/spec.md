# Feature Specification: Fix TLS Flag Naming

**Feature Branch**: `003-fix-tls-flag-naming`
**Created**: 2026-03-17
**Status**: Draft
**Input**: Rename inconsistent TLS/insecure flags in `jmp login` and related commands

## User Scenarios & Testing

### User Story 1 - Consistent flag naming for TLS options (Priority: P1)

A user running `jmp login` or `jmp admin create` should see consistently named
`--insecure-*` flags that clearly communicate their scope and effect. The current
flag `--insecure-tls-config` is ambiguous because "config" does not describe what
is being made insecure.

**Why this priority**: Confusing flag names lead to misconfiguration and reduced
trust in security-sensitive operations.

**Independent Test**: Run `jmp login --help` and verify the new flag names appear
with clear help text. Verify old flag names still work but emit a deprecation
warning.

**Acceptance Scenarios**:

1. **Given** a user runs `jmp login --help`, **When** viewing the output,
   **Then** the new flag names are displayed and the old names are hidden or
   marked deprecated.
2. **Given** a user runs `jmp login --insecure-tls-config`, **When** the command
   executes, **Then** it works but prints a deprecation warning to stderr.
3. **Given** a user runs `jmp login --insecure-tls`, **When** the command
   executes, **Then** it behaves identically to the old `--insecure-tls-config`
   without any deprecation warning.

---

### User Story 2 - Backward compatibility during transition (Priority: P1)

Existing scripts and documentation that use the old flag names must continue to
work during a deprecation period.

**Why this priority**: Breaking existing automation without warning violates user
trust.

**Independent Test**: Run existing e2e tests with old flag names and verify they
pass. Run them with new flag names and verify they also pass.

**Acceptance Scenarios**:

1. **Given** a CI script uses `--insecure-tls-config`, **When** the script runs,
   **Then** it succeeds and emits a deprecation warning.
2. **Given** documentation references old flag names, **When** a user follows
   the docs, **Then** the commands still work.

---

### Edge Cases

- What happens when both old and new flag names are provided simultaneously?
  The command should reject this with a clear error message.
- What happens when the deprecated flag is used in non-interactive mode?
  The deprecation warning should still be printed to stderr.

## Requirements

### Functional Requirements

- **FR-001**: The flag `--insecure-tls-config` MUST be renamed to `--insecure-tls`
  across all commands that use it (`jmp login`, `jmp admin create`, `jmp admin import`).
- **FR-002**: The flags `--insecure-login-tls` and `--insecure-login-http` MUST
  be kept as-is (they already follow the `--insecure-<scope>` convention).
- **FR-003**: The old flag name `--insecure-tls-config` MUST remain functional
  as a deprecated alias for at least one minor release cycle.
- **FR-004**: Using the deprecated alias MUST emit a warning message to stderr.
- **FR-005**: Documentation MUST be updated to use the new flag names.
- **FR-006**: The Python parameter name `insecure_tls_config` SHOULD be renamed
  to `insecure_tls` for internal consistency, with the Click option handling the
  mapping.

### Key Entities

- **opt_insecure_tls_config**: The shared Click option decorator in
  `jumpstarter_cli_common/opt.py`, used by login, create, and import commands.

## Clarifications

### Naming Convention Decision

**Recommendation**: Use the `--insecure-<scope>` pattern.

The three flags serve distinct purposes at different scopes:

| Current Name             | New Name               | Scope                          | Rationale                  |
|--------------------------|------------------------|--------------------------------|----------------------------|
| `--insecure-tls-config`  | `--insecure-tls`       | Endpoint gRPC TLS verification | "config" is redundant; the flag disables TLS verification for the stored endpoint config |
| `--insecure-login-tls`   | `--insecure-login-tls` | Login endpoint TLS verification | Already follows convention |
| `--insecure-login-http`  | `--insecure-login-http`| Login endpoint transport        | Already follows convention |

**Why `--insecure-tls` over `--insecure-endpoint-tls`**: The flag applies to
the general TLS configuration stored in the client/exporter config, not just a
specific endpoint during login. The shorter form `--insecure-tls` is clearer
because it is the "default" scope -- if no qualifier is given, it applies to
the primary TLS connection. The login-specific flags already carry the `login-`
qualifier to distinguish themselves.

**Alternative rejected**: `--insecure-endpoint-tls` was considered but rejected
because "endpoint" is ambiguous (login endpoint vs gRPC endpoint) and the extra
word adds length without clarity.

## Success Criteria

### Measurable Outcomes

- **SC-001**: `jmp login --help` shows `--insecure-tls` as the flag name.
- **SC-002**: All existing tests pass with the old flag name (backward compat).
- **SC-003**: All existing tests pass with the new flag name.
- **SC-004**: A deprecation warning is emitted when the old name is used.
- **SC-005**: Documentation files reference only the new flag name.
