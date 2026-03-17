# Data Model: Shell Completion

**Feature Branch**: `004-shell-completion`
**Date**: 2026-03-17

## Entities

This feature has a minimal data model. No persistent storage or complex data structures are involved.

### Supported Shells

The only domain concept is the set of supported shell types:

| Shell    | Click Class      | Complete Variable  |
|----------|------------------|--------------------|
| `bash`   | `BashComplete`   | `_JMP_COMPLETE`    |
| `zsh`    | `ZshComplete`    | `_JMP_COMPLETE`    |
| `fish`   | `FishComplete`   | `_JMP_COMPLETE`    |

These are represented as a Click `Choice` parameter rather than a custom enum or data class, following the Simplicity principle.

### Data Flow

```text
User input ("bash"|"zsh"|"fish")
  --> Click Choice validation
  --> get_completion_class(shell_name)
  --> ShellComplete(cli, ctx_args, prog_name, complete_var)
  --> .source() -> str
  --> stdout
```

No data is persisted, transformed, or stored. The output is a shell script string printed to stdout.
