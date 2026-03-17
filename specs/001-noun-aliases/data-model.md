# Data Model: CLI Noun Aliases

**Feature**: 001-noun-aliases
**Date**: 2026-03-17

## Alias Mapping Structure

The `common_aliases` dictionary in `AliasedGroup` maps canonical command names (keys) to lists of accepted alternative names (values). This is the sole data structure for alias resolution.

### Type Definition

```python
common_aliases: dict[str, list[str]]
```

- **Key**: The canonical command name (the name used in `@group.command("name")` or a logical canonical form)
- **Value**: A list of strings, each an accepted alias that resolves to the canonical command

### Updated Mapping

The following entries are relevant to noun aliasing. Entries marked with `[NEW]` are additions for this feature:

```python
common_aliases = {
    # ... existing verb aliases unchanged ...
    "remove": ["rm"],
    "list": ["ls"],
    "create": ["c"],
    "import": ["i"],
    "get": ["g"],
    "admin": ["a"],
    "create-config": ["cc"],
    "delete-config": ["dc"],
    "edit-config": ["ec"],
    "list-configs": ["lc"],
    "use-config": ["uc"],
    "move": ["mv"],
    "config": ["conf"],
    "delete": ["del", "d"],
    "shell": ["sh", "s"],

    # Noun aliases (bidirectional singular/plural)
    "exporter": ["exporters", "e"],      # existing
    "exporters": ["exporter"],           # [NEW] reverse for plural-registered commands
    "client": ["clients", "c"],          # existing
    "clients": ["client"],               # [NEW] reverse for plural-registered commands
    "lease": ["leases", "l"],            # existing
    "leases": ["lease"],                 # [NEW] reverse for plural-registered commands
    "cluster": ["clusters"],             # [NEW] for admin create/delete
    "clusters": ["cluster"],             # [NEW] reverse for admin contexts

    "version": ["ver", "v"],
}
```

### Resolution Semantics

1. A command is first looked up by its exact registered name
2. If not found, the alias table is consulted: for each canonical key that is also a registered command in the current group, check if the typed name appears in its alias list
3. The alias table is global (shared across all `AliasedGroup` instances) but only active entries matter -- an alias for `cluster` has no effect in a group that has no `cluster` command registered

### Important Constraint

The admin `get` group registers BOTH `cluster` (singular, get-one-by-name) and `clusters` (plural, list-all) as separate commands. Because both are registered as exact command names, `click.Group.get_command()` will find them directly in step 1, before alias resolution is attempted. The alias entries `cluster -> [clusters]` and `clusters -> [cluster]` will therefore NOT cause conflicts in this group, because both names resolve directly to their registered commands without ever reaching the alias lookup.

## Batch Delete Argument

The `delete leases` command argument changes from:

```python
# Before
@click.argument("name", required=False)

# After
@click.argument("names", nargs=-1)
```

The `names` parameter type changes from `str | None` to `tuple[str, ...]`. An empty tuple is treated the same as the previous `None` case (fall through to `--selector` or `--all`).
