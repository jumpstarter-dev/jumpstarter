# Quickstart: Shell Completion

**Feature Branch**: `004-shell-completion`
**Date**: 2026-03-17

## Prerequisites

- Jumpstarter CLI installed (`jmp` command available)
- One of: Bash 4.4+, Zsh 5.0+, or Fish 3.0+

## Installation

### Bash

Add to `~/.bashrc`:

```bash
eval "$(jmp completion bash)"
```

Or save to completions directory:

```bash
jmp completion bash > /etc/bash_completion.d/jmp
```

Reload your shell or run `source ~/.bashrc`.

### Zsh

Add to `~/.zshrc`:

```bash
eval "$(jmp completion zsh)"
```

Or save to your fpath:

```bash
jmp completion zsh > "${fpath[1]}/_jmp"
```

Reload your shell or run `source ~/.zshrc`.

### Fish

Save to Fish completions directory:

```bash
jmp completion fish > ~/.config/fish/completions/jmp.fish
```

Fish will pick it up automatically on next shell start.

## Testing

After installation, verify completion works:

```bash
jmp <TAB>
```

You should see available subcommands listed (e.g., `auth`, `config`, `create`, `shell`, etc.).

## Running Tests

```bash
make pkg-test-jumpstarter_cli
```

This runs the full test suite for the CLI package, including the completion command tests.
