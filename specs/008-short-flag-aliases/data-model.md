# Data Model: Flag Alias Mapping

**Branch**: `008-short-flag-aliases` | **Date**: 2026-03-17

## Flag Alias Mapping Table

This table defines the complete set of short-flag alias changes for this
feature. Each row represents a single `click.option` modification.

| Package             | File              | Command        | Long Flag   | New Short | Existing Shorts on Command |
|---------------------|-------------------|----------------|-------------|-----------|---------------------------|
| jumpstarter-cli     | `get.py`          | `get leases`   | `--all`     | `-a`      | `-l` (selector), `-o` (output) |
| jumpstarter-cli     | `delete.py`       | `delete leases` | `--all`    | `-a`      | `-l` (selector), `-o` (output) |
| jumpstarter-cli     | `auth.py`         | `auth status`  | `--verbose` | `-v`      | none                       |

## Invariants

- No two options on the same command share a short alias letter.
- Existing short aliases are never reassigned.
- Only flags with no conflicting short on their command receive a new alias.

## No Schema Changes

This feature modifies Click option declarations only. There are no data
model, database, API, or configuration file changes.
