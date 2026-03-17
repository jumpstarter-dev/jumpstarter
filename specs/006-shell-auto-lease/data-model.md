# Data Model: Shell Auto-Lease

**Branch**: `006-shell-auto-lease` | **Date**: 2026-03-17

## Lease Selection Flow

No new data models are required. The feature uses existing models:

### Existing Models Used

```
ClientConfigV1Alpha1
  +-- list_leases(only_active=True) -> LeaseList

LeaseList
  +-- leases: list[Lease]

Lease
  +-- name: str           # unique lease identifier
  +-- exporter: str        # exporter name connected to this lease
  +-- selector: str        # label selector used to create the lease
  +-- namespace: str
  +-- conditions: list[Condition]
  +-- effective_begin_time: datetime | None
  +-- effective_end_time: datetime | None
```

### Selection Logic State Machine

```
[jmp shell invoked without --selector, --name, --lease, $JMP_LEASE]
    |
    v
[Call config.list_leases(only_active=True)]
    |
    +-- 0 leases -> UsageError("No active leases found...")
    |
    +-- 1 lease  -> Set lease_name = lease.name
    |                Echo "Auto-connecting to lease: {name} (exporter: {exporter})"
    |                Continue to existing shell flow
    |
    +-- N leases -> [Check sys.stdin.isatty()]
                        |
                        +-- TTY: click.prompt with Choice -> set lease_name
                        |
                        +-- Not TTY: UsageError listing lease names
```

### Display Format for Picker

Each lease option in the interactive picker is displayed as:

```
{lease.name} (exporter: {lease.exporter})
```

This gives users enough context to distinguish between leases without
overwhelming them with details.
