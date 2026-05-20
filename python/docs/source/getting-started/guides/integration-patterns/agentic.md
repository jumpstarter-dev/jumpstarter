# Agentic

Jumpstarter exposes hardware control as structured {term}`MCP` tools, enabling
AI coding agents to interact with {term}`device`s using natural language from
IDEs and AI assistants.

```{mermaid}
flowchart TB
    subgraph "Developer"
        IDE["IDE / AI Assistant"]
    end

    subgraph "MCP Server"
        JmpMCP["jmp mcp serve"]
    end

    subgraph "Jumpstarter Infrastructure"
        DUTs["Device Under Test"]
    end

    IDE -- "MCP Protocol" --> JmpMCP
    JmpMCP -- "Lease & connect" --> DUTs
```

## Prerequisites

- Jumpstarter CLI ({term}`jmp`) installed and configured with a client identity
- An {term}`MCP`-compatible AI tool (Cursor, Claude Code, Claude Desktop, or any
  {term}`MCP` client)
- The `jumpstarter-mcp` package (included in a full install)

## Setup

### Cursor

Add to your Cursor {term}`MCP` configuration (`~/.cursor/mcp.json`):

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

```console
claude mcp add jumpstarter -- jmp mcp serve
```

### Claude Desktop

Add to your Claude Desktop configuration:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

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

### Other Clients

Any {term}`MCP`-compatible client can use the Jumpstarter server. It
communicates over stdio:

```console
jmp mcp serve
```

For the full list of available tools and their parameters, see the
[MCP package reference](../../../reference/package-apis/mcp.md).

## Usage Examples

### Interactive Hardware Exploration

> **You**: What devices are available on the cluster?
>
> *Agent calls `jmp_list_exporters` and shows a summary of available hardware.*
>
> **You**: Get me a QEMU target and power it on.
>
> *Agent calls `jmp_create_lease`, `jmp_connect`, then `jmp_run` with
> `["power", "on"]`.*
>
> **You**: Check what OS is running via SSH.
>
> *Agent calls `jmp_run` with `["ssh", "--", "cat", "/etc/os-release"]`.*

### Claude Code Session

```
$ claude

> /mcp

Connected MCP servers:
  - jumpstarter (jmp mcp serve)

> Can you list the hardware available on the jumpstarter cluster?

I'll check what devices are available...

[Uses jmp_list_exporters]

Here's what's available:
  - qemu-test-01 (online, no active lease)
  - arm-board-01 (online, leased by alice)
  - arm-board-02 (online, no active lease)

> Lease arm-board-02 and check if it boots to Linux

[Uses jmp_create_lease, jmp_connect, jmp_run to power on and SSH]

The board is running Fedora 41 (aarch64). Here's the full `uname -a` output...
```

### Cursor Agent Mode

In Cursor's Composer (Agent mode), the Jumpstarter tools are available
alongside your code:

1. Ask the agent to flash a new firmware image to a board
2. Have it verify the board boots successfully via serial console
3. Run your test suite against the live hardware
4. Iterate on code fixes with the agent retesting on real hardware

## Typical Workflow

```{mermaid}
sequenceDiagram
    participant User
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Ctrl as Controller

    User->>Agent: "Get me an ARM board"
    Agent->>MCP: jmp_create_lease(selector="arch=arm64")
    MCP->>Ctrl: Request lease
    Ctrl-->>MCP: Lease ID
    MCP-->>Agent: Lease created

    Agent->>MCP: jmp_connect(lease_id)
    MCP-->>Agent: Connected

    Agent->>MCP: jmp_explore()
    MCP-->>Agent: Available commands: power, ssh, serial, storage

    User->>Agent: "Power it on and check the OS"
    Agent->>MCP: jmp_run(["power", "on"])
    Agent->>MCP: jmp_run(["ssh", "--", "cat", "/etc/os-release"])
    MCP-->>Agent: OS info

    User->>Agent: "Done, release it"
    Agent->>MCP: jmp_disconnect()
    Agent->>MCP: jmp_delete_lease()
```

## Tips

- **Use `jmp_explore` first** -- each {term}`device` type exposes different
  commands
- **Set `timeout_seconds` for streaming commands** -- commands like `serial pipe`
  block indefinitely
- **Use `jmp_drivers` for Python access** -- inspect the driver tree to discover
  methods and signatures
- **Connections are persistent** -- create once, run many commands

## Logging

The {term}`MCP` server logs to `~/.jumpstarter/logs/mcp-server.log`:

```console
tail -f ~/.jumpstarter/logs/mcp-server.log
```
