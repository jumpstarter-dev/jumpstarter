# jumpstarter-mcp

MCP (Model Context Protocol) server for AI agent interaction with Jumpstarter
hardware devices.

## Overview

This package provides an MCP server that exposes Jumpstarter's lease management,
device connections, and command execution as structured tools accessible by AI
agents (e.g., via Cursor, Claude Code, or any MCP-compatible host).

## IDE Integration

### Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "jumpstarter": {
      "command": "jmp",
      "args": ["mcp", "serve"]
    }
  }
}
```

### Claude Code

Claude Code discovers MCP servers from its configuration. Add Jumpstarter
with:

```bash
claude mcp add jumpstarter -- jmp mcp serve
```

Or manually add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "jumpstarter": {
      "command": "jmp",
      "args": ["mcp", "serve"]
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop configuration file
(`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "jumpstarter": {
      "command": "jmp",
      "args": ["mcp", "serve"]
    }
  }
}
```

## Available Tools

### Lease & Exporter Management

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

### Device Interaction

| Tool | Description |
|---|---|
| `jmp_run` | Execute CLI commands on a connected device |
| `jmp_get_env` | Get environment and code examples for direct access |

### Discovery & Introspection

| Tool | Description |
|---|---|
| `jmp_explore` | Discover available CLI commands on a device |
| `jmp_drivers` | List driver objects and their methods |
| `jmp_driver_methods` | Inspect driver method signatures and docstrings |

## Typical Workflow

A typical interaction with an AI agent looks like this:

1. **List exporters** to see what hardware is available:
   > "What devices are available on the cluster?"

2. **Create a lease** for a target device:
   > "Get me a QEMU target" or "Lease a board with label board-type=qc8650"

3. **Connect** to establish a persistent connection:
   > "Connect to that lease"

4. **Interact** with the device:
   > "Power on the target and check what OS it's running via SSH"

5. **Disconnect and release** when done:
   > "Disconnect and delete the lease"

## Writing Python with AI Assistance

The MCP server is especially useful when writing Python code that interacts with
hardware. While connected to a device, the agent can introspect the live
connection to discover available drivers, methods, and their signatures -- then
use that knowledge to help you write correct code.

**Ask the agent to explore what's available on your target:**

> "I'm connected to an ARM board. What drivers and methods are available?"
>
> *The agent calls `jmp_drivers` and `jmp_driver_methods` to inspect the live
> connection and gives you a summary of power, ssh, serial, storage, etc.*

**Ask for help writing automation scripts:**

> "Write me a Python script that power-cycles the board, waits for it to boot,
> and grabs the kernel version over SSH."
>
> *The agent inspects the driver methods to discover exact signatures and
> generates a working script using the `env()` helper.*

**Debug a failing interaction:**

> "My serial expect is timing out. Can you read the serial output and tell me
> what the board is printing?"
>
> *The agent calls `jmp_run` with `["serial", "pipe"]` and a short timeout
> to capture what the console is outputting right now.*

**Discover capabilities you didn't know about:**

> "What can I do with the storage driver on this device?"
>
> *The agent calls `jmp_driver_methods` for the storage driver and shows you
> methods like `flash`, `write_local_file`, `read_to_local_file`, etc. with
> their full signatures and docstrings.*

**Iterate on code with live hardware feedback:**

> "Run my test script and tell me if the board boots successfully."
>
> *The agent uses `jmp_get_env` to get the shell environment, executes your
> script, and reports back with the actual device output.*

## Logging

The MCP server logs to `~/.jumpstarter/logs/mcp-server.log`. To monitor:

```bash
tail -f ~/.jumpstarter/logs/mcp-server.log
```
