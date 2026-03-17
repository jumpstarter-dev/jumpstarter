# Contract: CLI Shell Command

**Branch**: `006-shell-auto-lease` | **Date**: 2026-03-17

## Updated Shell Command Behavior

### Command Signature (unchanged)

```
jmp shell [OPTIONS] [COMMAND]...
```

### Options (unchanged)

- `--lease` / `LEASE_NAME`: Connect to a specific pre-existing lease
- `-l` / `--selector`: Label selector for lease creation
- `-n` / `--name`: Target exporter by name
- `--duration`: Lease duration (default: 30m)
- `--exporter-logs`: Enable log streaming
- `--acquisition-timeout`: Override acquisition timeout

### New Behavior: No Flags Provided

When `config` is `ClientConfigV1Alpha1` and none of `--selector`, `--name`,
`--lease`, or `$JMP_LEASE` are set:

#### Zero Active Leases

```
Error: No active leases found.
Create a lease with: jmp shell --selector/-l <selector>
Or target an exporter: jmp shell --name/-n <exporter>
```

Exit code: 2 (Click UsageError)

#### One Active Lease

```
Auto-connecting to lease: <name> (exporter: <exporter>)
```

Proceeds to connect using that lease. The lease is NOT released on exit
(same behavior as `--lease`).

#### Multiple Active Leases (TTY)

```
Multiple active leases found. Select one:
  1) lease-abc123 (exporter: device-1)
  2) lease-def456 (exporter: device-2)
  3) lease-ghi789 (exporter: device-3)
Choose lease [1-3]:
```

User enters number, proceeds with selected lease.

#### Multiple Active Leases (non-TTY)

```
Error: Multiple active leases found. Specify one with --lease:
  - lease-abc123 (exporter: device-1)
  - lease-def456 (exporter: device-2)
  - lease-ghi789 (exporter: device-3)
```

Exit code: 2 (Click UsageError)

### Backward Compatibility

All existing flag combinations work identically. The new behavior only
activates when no targeting flags are provided.

### Function Contract

```python
def _select_lease_from_active(config: ClientConfigV1Alpha1) -> str:
    """Query active leases and return a lease name to connect to.

    Returns the lease name (str) for auto-connection.

    Raises:
        click.UsageError: When no leases exist or multiple leases
                          exist on a non-TTY.
    """
```

This function is called from the `shell` Click command, before the
existing `anyio.run(...)` call. It sets `lease_name` for the existing
flow.
