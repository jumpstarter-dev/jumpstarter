# MCP

MCP server for AI agent interaction with Jumpstarter hardware devices.

For setup instructions and usage examples, see the
[Agentic Integration](../../getting-started/guides/integration-patterns/agentic.md)
guide.

## Available Tools

### Lease and Exporter Management

| Tool | Description |
|---|---|
| `jmp_list_exporters` | List exporters with online status and lease info |
| `jmp_list_leases` | List active leases |
| `jmp_create_lease` | Create a new lease by selector or exporter name |
| `jmp_delete_lease` | Release a lease |

### Connection Management

| Tool | Description |
|---|---|
| `jmp_connect` | Connect to a device (by lease, selector, or exporter) |
| `jmp_disconnect` | Disconnect from a device |
| `jmp_list_connections` | List active connections |
| `jmp_list_events` | Return and remove up to `max_count` (default 50) buffered events: lease expiry warnings, transfers, and connection failures |

### Device Interaction

| Tool | Description |
|---|---|
| `jmp_run` | Execute CLI commands on a connected device |
| `jmp_get_env` | Get environment and code examples for direct access |

### Discovery and Introspection

| Tool | Description |
|---|---|
| `jmp_explore` | Discover available CLI commands on a device |
| `jmp_drivers` | List driver objects and their methods |
| `jmp_driver_methods` | Inspect driver method signatures and docstrings |

## API Reference

```{eval-rst}
.. automodule:: jumpstarter_mcp.server
   :members:
   :undoc-members:
```
