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
| `jmp_tools` | List a connected {term}`device`'s drivers and their tools; one driver's tools with their arguments |
| `jmp_read` | Call a tool that only reads |
| `jmp_call` | Call any tool of the {term}`device` |
| `jmp_run` | Execute CLI commands on a connected {term}`device` |
| `jmp_get_env` | Get environment and code examples for direct access |

### Discovery and Introspection

| Tool | Description |
|---|---|
| `jmp_explore` | Discover available CLI commands on a {term}`device` |
| `jmp_drivers` | List driver objects and their methods |
| `jmp_driver_methods` | Inspect driver method signatures and docstrings |

## Tools made from the drivers

What a device can do is known once it is connected, and differs from one device to the next. The server makes a typed
tool of each public member of each driver's client, from the same list Robot Framework makes its keywords from
(`jumpstarter.client.introspect.iter_client_members`): a method is a tool, a property is a tool that reads it, and a
generator is a tool that reads up to `max_items` of it. A tool's description is the method's docstring, the
descriptions of its arguments are the docstring's `Args:` section, and their JSON schema comes from the type hints.

They are reached through three fixed tools, so they work in every agent host, whatever it does with a list of tools
that changes while it runs, and so that none of them costs anything until it is asked for:

- `jmp_connect` answers with the names of the device's tools, by driver, and nothing more;
- `jmp_tools(connection_id, driver)` gives one driver's tools with their arguments;
- `jmp_read(connection_id, tool, arguments)` calls a tool that only reads, and `jmp_call` any tool. A host can let
  the first pass and ask a person before the second.

A tool is named by its driver's path and its command (`dutlink_power_on`), never by its lease. Every call names its
`connection_id`: with several devices connected, that is what says which one a tool acts on, and two devices with the
same driver have the same tools. Every answer says which connection, exporter and lease it is about.

A driver's author curates its tools with `jumpstarter.client.decorators.mcp`:

```python
from jumpstarter.client.decorators import mcp

class PowerClient(DriverClient):
    @mcp.tool(read_only=True)
    def read(self): ...

    @mcp.tool(description="Power the device off, wait, and on again.", destructive=True)
    def cycle(self, wait: int = 2): ...

    @mcp.not_tool
    def raw(self): ...
```

`jmp_run` still runs a raw `j` command line, for what is not one call (a console that keeps streaming).

## API Reference

```{eval-rst}
.. automodule:: jumpstarter_mcp.server
   :members:
   :undoc-members:
```
