# Contract: CLI Noun Aliases

**Feature**: 001-noun-aliases
**Date**: 2026-03-17

## Noun Alias Contract

This contract defines which noun forms are accepted by each CLI command group and how they resolve.

### Definitions

- **Canonical name**: The name passed to `@group.command("name")` when registering the command
- **Alias**: An alternative name that resolves to the canonical name via `AliasedGroup`
- **Bidirectional**: Both singular and plural forms work regardless of which is canonical

### User CLI Noun Table

| Command | Canonical Name | Accepted Aliases | Short Alias |
|---------|---------------|-----------------|-------------|
| `jmp get exporters` | `exporters` | `exporter` | `e` |
| `jmp get leases` | `leases` | `lease` | `l` |
| `jmp create lease` | `lease` | `leases` | `l` |
| `jmp update lease` | `lease` | `leases` | `l` |
| `jmp delete leases` | `leases` | `lease` | `l` |

### Admin CLI Noun Table

| Command | Canonical Name | Accepted Aliases | Short Alias |
|---------|---------------|-----------------|-------------|
| `jmp admin get client` | `client` | `clients` | `c` |
| `jmp admin get exporter` | `exporter` | `exporters` | `e` |
| `jmp admin get lease` | `lease` | `leases` | `l` |
| `jmp admin get cluster` | `cluster` | NONE (distinct command) | -- |
| `jmp admin get clusters` | `clusters` | NONE (distinct command) | -- |
| `jmp admin create client` | `client` | `clients` | `c` |
| `jmp admin create exporter` | `exporter` | `exporters` | `e` |
| `jmp admin create cluster` | `cluster` | `clusters` | -- |
| `jmp admin delete client` | `client` | `clients` | `c` |
| `jmp admin delete exporter` | `exporter` | `exporters` | `e` |
| `jmp admin delete cluster` | `cluster` | `clusters` | -- |

### Exception: Admin Get Cluster vs Clusters

`jmp admin get cluster <name>` and `jmp admin get clusters` are semantically different commands:
- `cluster` retrieves a single cluster by name
- `clusters` lists all clusters

Because both are registered as separate commands in the same group, Click resolves them by exact name match before alias lookup occurs. The alias entries in `common_aliases` are therefore inert for this specific group and do not cause conflicts.

### Batch Delete Contract

The `jmp delete leases` command accepts zero or more positional NAME arguments:

```
jmp delete leases [NAME...] [--selector SELECTOR] [--all] [--output name]
```

**Behavior**:
- Zero names + no selector + no --all: error ("One of NAME, --selector or --all must be specified")
- One or more names: delete each named lease sequentially
- --selector: delete leases matching the label selector
- --all: delete all leases owned by the current client
- Names and --selector/--all are mutually exclusive (names take precedence if provided)

**Output**:
- Default: `lease "<name>" deleted` for each deleted lease
- `--output name`: bare name printed for each deleted lease, one per line
