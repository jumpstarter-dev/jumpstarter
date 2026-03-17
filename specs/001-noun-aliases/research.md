# Research: CLI Noun Aliases

**Feature**: 001-noun-aliases
**Date**: 2026-03-17

## AliasedGroup Class Analysis

### Location
`python/packages/jumpstarter-cli-common/jumpstarter_cli_common/alias.py`

### How It Works

`AliasedGroup` extends `click.Group` and overrides `get_command()` to resolve aliases. The resolution flow is:

1. Try to find the command by its exact registered name via `click.Group.get_command()`
2. If not found, search `common_aliases` for any canonical command name whose alias list contains the typed name
3. If exactly one match is found, return that command
4. If multiple matches, fail with ambiguity error
5. If no matches, return None (Click shows "No such command" error)

### Current Alias Mapping

```python
common_aliases = {
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
    "exporter": ["exporters", "e"],
    "client": ["clients", "c"],
    "lease": ["leases", "l"],
    "version": ["ver", "v"],
}
```

### Noun Subcommands Inventory

**User CLI (`jmp`)**:
| Command Group | Registered Noun | Canonical Form | Aliases Needed |
|--------------|-----------------|---------------|----------------|
| `get` | `exporters` (plural) | plural | `exporter` (singular) |
| `get` | `leases` (plural) | plural | `lease` (singular) |
| `delete` | `leases` (plural) | plural | `lease` (singular) |
| `create` | `lease` (singular) | singular | `leases` (plural) |
| `update` | `lease` (singular) | singular | `leases` (plural) |

**Admin CLI (`jmp admin`)**:
| Command Group | Registered Noun | Canonical Form | Aliases Needed |
|--------------|-----------------|---------------|----------------|
| `get` | `client` (singular) | singular | `clients` (plural) -- but see note |
| `get` | `exporter` (singular) | singular | `exporters` (plural) -- but see note |
| `get` | `lease` (singular) | singular | `leases` (plural) |
| `get` | `cluster` (singular) | singular | NONE -- `clusters` is a separate command |
| `get` | `clusters` (plural) | plural | NONE -- `cluster` is a separate command |
| `create` | `client` (singular) | singular | already aliased |
| `create` | `exporter` (singular) | singular | already aliased |
| `create` | `cluster` (singular) | singular | `clusters` (plural) |
| `delete` | `client` (singular) | singular | already aliased |
| `delete` | `exporter` (singular) | singular | already aliased |
| `delete` | `cluster` (singular) | singular | `clusters` (plural) |

### Key Finding: Bidirectional Alias Gap

The current `common_aliases` maps singular -> plural (e.g., `exporter -> [exporters]`). But some commands are registered under their **plural** name (e.g., `get exporters`, `get leases`, `delete leases`). When the user types the singular form for these, the alias lookup works because `exporter` is the canonical key and `exporters` is the registered command name -- but wait, the alias system resolves "exporters" as an alias of the canonical key "exporter". If the registered command is "exporters" (plural), then typing "exporter" (the canonical key) would find it directly via `click.Group.get_command()` only if "exporter" is the registered name.

Let me trace through the actual resolution for `jmp get exporter`:
1. `get_command(ctx, "exporter")` is called
2. `click.Group.get_command(self, ctx, "exporter")` -- looks for a command registered as "exporter". The command is registered as "exporters", so this returns None.
3. Search aliases: look for canonical commands whose aliases include "exporter". No canonical command has "exporter" in its alias list (it IS a canonical key, not an alias).
4. Result: command not found.

This means the current aliases do NOT actually solve singular -> plural for commands registered under their plural name. The fix must add reverse mappings: `"exporters": ["exporter"]` and `"leases": ["lease"]`.

### Batch Delete Analysis

The current `delete leases` command accepts `click.argument("name", required=False)` -- a single optional name. To support batch delete:

1. Change to `click.argument("names", nargs=-1)` -- accepts zero or more names as a tuple
2. Update the logic to iterate over all provided names
3. The existing `--selector` and `--all` flags remain as alternatives to explicit names
4. When names tuple is non-empty, delete each one; otherwise fall through to selector/all logic

### Click nargs=-1 Behavior

With `nargs=-1`, Click collects all remaining positional arguments into a tuple. Key behaviors:
- Empty invocation: `names` is an empty tuple `()`
- Single name: `names` is `("lease1",)`
- Multiple names: `names` is `("lease1", "lease2", "lease3")`
- Works correctly with options that follow (Click handles `--` separator if needed)

## Conclusion

The implementation requires:
1. Adding reverse plural -> singular aliases to `common_aliases` for nouns registered under plural names
2. Adding `cluster -> [clusters]` alias for admin create/delete
3. Changing `delete leases` argument from `required=False` single to `nargs=-1` variadic
4. NOT aliasing admin `get cluster` <-> `get clusters` since they are semantically different commands
