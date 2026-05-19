# MCP

{term}`MCP` server for AI agent interaction with Jumpstarter hardware devices.

For setup instructions and usage examples, see the
[Agentic Integration](../../getting-started/guides/integration-patterns/agentic.md)
guide.

## Available Tools

### Lease and Exporter Management

| Tool | Description |
|---|---|
| `jmp_list_exporters` | List {term}`exporter`s with online status and {term}`lease` info |
| `jmp_list_leases` | List active {term}`lease`s |
| `jmp_create_lease` | Create a new {term}`lease` by selector or {term}`exporter` name |
| `jmp_delete_lease` | Release a {term}`lease` |

### Connection Management

| Tool | Description |
|---|---|
| `jmp_connect` | Connect to a {term}`device` (by {term}`lease`, selector, or {term}`exporter`) |
| `jmp_disconnect` | Disconnect from a {term}`device` |
| `jmp_list_connections` | List active connections |

### Device Interaction

| Tool | Description |
|---|---|
| `jmp_run` | Execute CLI commands on a connected {term}`device` |
| `jmp_get_env` | Get environment and code examples for direct access |

### Discovery and Introspection

| Tool | Description |
|---|---|
| `jmp_explore` | Discover available CLI commands on a {term}`device` |
| `jmp_drivers` | List driver objects and their methods |
| `jmp_driver_methods` | Inspect driver method signatures and docstrings |

## API Reference

```{eval-rst}
.. automodule:: jumpstarter_mcp.server
   :members:
   :undoc-members:
```
