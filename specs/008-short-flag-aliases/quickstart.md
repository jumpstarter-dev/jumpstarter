# Quickstart: Short Flag Aliases

**Branch**: `008-short-flag-aliases` | **Date**: 2026-03-17

## Overview

This feature adds short single-letter aliases to commonly used CLI flags
that currently only have long-form names. The changes are small,
isolated modifications to Click option declarations.

## Files to Modify

1. `python/packages/jumpstarter-cli/jumpstarter_cli/get.py` -- add `-a`
   to `--all` on `get_leases`
2. `python/packages/jumpstarter-cli/jumpstarter_cli/delete.py` -- add `-a`
   to `--all` on `delete_leases`
3. `python/packages/jumpstarter-cli/jumpstarter_cli/auth.py` -- add `-v`
   to `--verbose` on `token_status`

## How to Implement

Each change follows the same pattern. In the `@click.option(...)` decorator,
insert the short alias as the first positional string argument before the
long flag:

```python
# Before
@click.option("--all", "show_all", is_flag=True, ...)

# After
@click.option("-a", "--all", "show_all", is_flag=True, ...)
```

## How to Test

Run the existing test suite plus new tests:

```bash
make pkg-test-jumpstarter_cli
```

Each new alias should have a test that uses Click's `CliRunner` to invoke
the command with the short flag and asserts the expected parameter value.

## How to Verify

```bash
# Should show all leases (including expired)
jmp get leases -a

# Should delete all owned leases
jmp delete leases -a

# Should show verbose token details
jmp auth status -v
```
