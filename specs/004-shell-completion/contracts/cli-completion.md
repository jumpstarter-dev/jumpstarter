# Contract: CLI Completion Subcommand

**Feature Branch**: `004-shell-completion`
**Date**: 2026-03-17

## Command Interface

### `jmp completion <shell>`

**Description**: Generate a shell completion script for the specified shell.

**Arguments**:

| Argument | Type   | Required | Values                  | Description                      |
|----------|--------|----------|-------------------------|----------------------------------|
| `shell`  | Choice | Yes      | `bash`, `zsh`, `fish`   | Target shell for completion      |

**Output**: Prints the completion script to stdout.

**Exit Codes**:

| Code | Meaning                                      |
|------|----------------------------------------------|
| 0    | Completion script generated successfully     |
| 2    | Invalid usage (missing or invalid argument)  |

### Examples

```bash
# Generate and source bash completions
eval "$(jmp completion bash)"

# Generate and save zsh completions
jmp completion zsh > ~/.zfunc/_jmp

# Generate and save fish completions
jmp completion fish > ~/.config/fish/completions/jmp.fish

# Invalid shell shows error
jmp completion powershell
# Error: Invalid value for 'SHELL': 'powershell' is not one of 'bash', 'zsh', 'fish'.
```

### Integration Points

- The `completion` command is registered on the `jmp` Click group in `jmp.py`.
- The command uses `click.shell_completion.get_completion_class()` to obtain the shell-specific completion class.
- The completion script references the `jmp` program name and the `_JMP_COMPLETE` environment variable.

### Module Location

- **Command**: `python/packages/jumpstarter-cli/jumpstarter_cli/completion.py`
- **Tests**: `python/packages/jumpstarter-cli/jumpstarter_cli/completion_test.py`
- **Registration**: `python/packages/jumpstarter-cli/jumpstarter_cli/jmp.py`
