# Feature Specification: Shell Completion

**Feature Branch**: `004-shell-completion`
**Created**: 2026-03-17
**Status**: Draft
**Input**: User description: "Add `jmp completion [bash|zsh|fish]` subcommand for shell completion support"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Generate Bash Completion Script (Priority: P1)

A user wants to enable tab completion for the `jmp` CLI in their Bash shell. They run `jmp completion bash` and receive a completion script they can source or install.

**Why this priority**: Bash is the most common shell on Linux systems, which is the primary platform for Jumpstarter.

**Independent Test**: Can be fully tested by running `jmp completion bash` and verifying it outputs a valid Bash completion script.

**Acceptance Scenarios**:

1. **Given** a user with Bash shell, **When** they run `jmp completion bash`, **Then** a Bash completion script is printed to stdout.
2. **Given** a user who sources the output of `jmp completion bash`, **When** they type `jmp ` and press Tab, **Then** they see available subcommands.

---

### User Story 2 - Generate Zsh Completion Script (Priority: P1)

A user wants to enable tab completion for the `jmp` CLI in their Zsh shell.

**Why this priority**: Zsh is the default shell on macOS, a supported platform.

**Independent Test**: Can be fully tested by running `jmp completion zsh` and verifying it outputs a valid Zsh completion script.

**Acceptance Scenarios**:

1. **Given** a user with Zsh shell, **When** they run `jmp completion zsh`, **Then** a Zsh completion script is printed to stdout.

---

### User Story 3 - Generate Fish Completion Script (Priority: P2)

A user wants to enable tab completion for the `jmp` CLI in their Fish shell.

**Why this priority**: Fish is a popular alternative shell with good completion support.

**Independent Test**: Can be fully tested by running `jmp completion fish` and verifying it outputs a valid Fish completion script.

**Acceptance Scenarios**:

1. **Given** a user with Fish shell, **When** they run `jmp completion fish`, **Then** a Fish completion script is printed to stdout.

---

### User Story 4 - Unsupported Shell Error (Priority: P2)

A user runs `jmp completion` with an unsupported shell name or no argument.

**Why this priority**: Good error handling improves user experience.

**Independent Test**: Can be tested by running `jmp completion` with no args or an invalid shell name.

**Acceptance Scenarios**:

1. **Given** a user, **When** they run `jmp completion` with no arguments, **Then** they see a usage message listing supported shells.
2. **Given** a user, **When** they run `jmp completion powershell`, **Then** they see an error indicating the shell is not supported.

---

### Edge Cases

- What happens when the completion command is run but the CLI entry point name differs (e.g., `j` alias)?
- How does the system handle generating completions for an unknown shell type?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a `completion` subcommand under the `jmp` CLI group.
- **FR-002**: System MUST support `bash`, `zsh`, and `fish` as shell arguments.
- **FR-003**: System MUST output a shell-specific completion script to stdout when given a valid shell name.
- **FR-004**: System MUST use Click's built-in `click.shell_completion` module to generate completion scripts.
- **FR-005**: System MUST show an error for unsupported shell names via Click's choice validation.
- **FR-006**: The `completion` subcommand MUST also be available on the `j` alias command.

### Key Entities

- **Shell**: One of `bash`, `zsh`, or `fish` -- the target shell for completion script generation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `jmp completion bash` outputs a non-empty Bash completion script.
- **SC-002**: `jmp completion zsh` outputs a non-empty Zsh completion script.
- **SC-003**: `jmp completion fish` outputs a non-empty Fish completion script.
- **SC-004**: All completion generation is covered by unit tests.
