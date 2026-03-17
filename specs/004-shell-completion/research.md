# Research: Shell Completion for Click CLI

**Feature Branch**: `004-shell-completion`
**Date**: 2026-03-17

## Click Shell Completion Overview

Click provides built-in shell completion support through the `click.shell_completion` module. This module supports three shells out of the box: Bash, Zsh, and Fish.

### Key API

- `click.shell_completion.get_completion_class(shell_name)` -- returns the completion class for a given shell name (`"bash"`, `"zsh"`, `"fish"`), or `None` if not found.
- Each shell class (`BashComplete`, `ZshComplete`, `FishComplete`) inherits from `ShellComplete`.
- Instantiation: `cls(cli=click_group, ctx_args={}, prog_name="jmp", complete_var="_JMP_COMPLETE")`
- `instance.source()` -- returns the shell-specific completion script as a string.

### How It Works

1. The `source()` method generates a shell script that sets up completion hooks.
2. The generated script sets environment variables (e.g., `_JMP_COMPLETE=bash_complete`) and invokes the CLI program to get completions dynamically.
3. Users source the generated script in their shell configuration (e.g., `~/.bashrc`, `~/.zshrc`) or save it to the appropriate completions directory.

### Generated Script Details

- **Bash** (~650 chars): Uses `COMP_WORDS` and `COMP_CWORD` environment variables. Registers via `complete -o nosort -F`.
- **Zsh** (~1100 chars): Uses `compdef` and `compadd`. Supports completion descriptions.
- **Fish** (~590 chars): Uses `commandline -cp` and `complete -c` function registration.

### Complete Variable Convention

Click uses the environment variable `_{PROG_NAME}_COMPLETE` (uppercase) to trigger completion mode. For `jmp`, this is `_JMP_COMPLETE`.

### No Additional Dependencies

The `click.shell_completion` module is part of Click itself. Since the CLI already depends on Click, no additional dependencies are needed. This aligns with the constitution's Minimal Dependencies principle.

## Implementation Approach

The simplest approach is to create a `completion` command that:
1. Accepts a shell name as a Click `Choice` argument (restricting to `bash`, `zsh`, `fish`).
2. Uses `get_completion_class()` to get the appropriate completion class.
3. Instantiates it with the root `jmp` CLI group and calls `source()`.
4. Prints the result to stdout.

This approach uses zero additional dependencies and leverages Click's own completion infrastructure.
