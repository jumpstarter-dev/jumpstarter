# AI Agent Integration

Jumpstarter provides an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/)
server that exposes hardware control as structured tools accessible by AI coding
agents. This enables natural-language-driven hardware interaction from IDEs and
AI assistants.

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
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

- Jumpstarter CLI (`jmp`) installed and configured with a client identity
- An MCP-compatible AI tool (Cursor, Claude Code, Claude Desktop, or any
  MCP client)

The MCP server package, which is normally provided when you perform a full install
through the `jumpstarter-mcp`package which provides the `jmp mcp serve` subcommand on the CLI.

## Setup

### Cursor

Add to your Cursor MCP configuration (`~/.cursor/mcp.json`):

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

Restart Cursor, then verify the server appears in **Settings > MCP**. The
Jumpstarter tools will be available to the AI agent in Composer.

### Claude Code

Register the MCP server with a single command:

```bash
claude mcp add jumpstarter -- jmp mcp serve
```

This writes the configuration to `~/.claude.json`. Verify with:

```bash
claude mcp list
```

Alternatively, you can add it manually to `~/.claude.json`:

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

Restart Claude Desktop and the Jumpstarter tools will appear in the tools menu.

### Other MCP Clients

Any MCP-compatible client can use the Jumpstarter server. The server
communicates over stdio using the standard MCP protocol. Launch it with:

```bash
jmp mcp serve
```

## Available Tools

The MCP server exposes the following tools:

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
| `jmp_get_env` | Get shell/Python environment for direct device access |

### Discovery & Introspection

| Tool | Description |
|---|---|
| `jmp_explore` | Discover available CLI commands and their arguments |
| `jmp_drivers` | List Python driver objects and their methods |
| `jmp_driver_methods` | Inspect method signatures, docstrings, and parameters |

## Usage Examples

### Example: Interactive Hardware Exploration

Once the MCP server is configured, you can interact with hardware using natural
language from your AI assistant:

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
> *Agent calls `jmp_run` with `["ssh", "--", "cat", "/etc/os-release"]` and
> interprets the output.*
>
> **You**: Give me a Python example to automate this.
>
> *Agent calls `jmp_get_env` and generates a script using the `env()` helper.*

### Example: Claude Code Session

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

### Example: Cursor Agent Mode

In Cursor's Composer (Agent mode), the Jumpstarter tools are available
alongside your code. This enables workflows like:

1. Ask the agent to flash a new firmware image to a board
2. Have it verify the board boots successfully via serial console
3. Run your test suite against the live hardware
4. Iterate on code fixes with the agent retesting on real hardware

## Typical Workflow

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
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

- **Use `jmp_explore` first**: Each device type exposes different commands.
  Always explore before assuming what's available.
- **Set `timeout_seconds` for streaming commands**: Commands like `serial pipe`
  block indefinitely. Use a short `timeout_seconds` (e.g., 10-15) so the
  command is killed after capturing available output.
- **Use `jmp_drivers` for Python access**: When you need programmatic control
  beyond CLI commands, inspect the Python driver tree to discover available
  methods and their signatures.
- **Connections are persistent**: Create once, run many commands. No need to
  reconnect between commands.

## Logging and Debugging

The MCP server logs to `~/.jumpstarter/logs/mcp-server.log`. Monitor it with:

```bash
tail -f ~/.jumpstarter/logs/mcp-server.log
```

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

See the [jumpstarter-mcp package reference](../../reference/package-apis/mcp.md)
for the full list of tools and their parameters.
