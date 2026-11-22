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
| `jmp_environments` | List the environments the device tools can run in, and which each connection belongs to |
| `jmp_use_environment` | Choose the environment the device tools run in |

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

## Install once, drivers per project

```shell
uv tool install jumpstarter-mcp        # the server, once: the `jumpstarter-mcp` command
```

Register `jumpstarter-mcp` in your agent's host. The drivers of your devices go in a Python environment of their
own, per project, and nothing else has to be in it:

```shell
cd my-project
uv venv
uv pip install jumpstarter-driver-power jumpstarter-driver-pyserial   # what your devices need
```

Launch your agent in that project: the server uses the `.venv` of the directory it is launched in (or the
environment that is active), with nothing more to set up. To use another, start the server with `--env`, or name it
in the environments file. The server runs the device tools in that environment. It does not have to
be installed there: an environment that has only drivers is lent the server for the length of a run (`uv` lays the
package over the environment's Python and leaves the environment as it was). An environment that has
`jumpstarter-mcp` installed runs its own.

`jmp mcp serve` is the same server, for an environment that has the `jmp` command.

## One server, many environments

A device's drivers are Python packages: a project installs the ones its devices need, in its own environment or
container. The server an agent's host starts does not have to live there. It may be installed once, with no driver
at all; when a tool needs the drivers, it starts a second copy of itself where they are and passes the call on, over
the same protocol the host speaks to it.

An environment is anywhere `jmp mcp serve` can be started:

| | |
| --- | --- |
| a path | a Python environment's directory, or a project directory that has a `.venv` |
| `container:<name>` | a container that runs (the server is started in it with `podman exec`, or `docker`) |
| `image:<reference>` | a container started for it |
| a name | an entry of the environments file: one of the above, or any command that starts the server |

The environments file is `mcp-environments.yaml` beside the client configurations (or the file
`JMP_MCP_ENVIRONMENTS` names):

```yaml
environments:
  phones: {path: ~/work/phones}
  rig-a: {container: rig-a-dev}
  lab: {command: [ssh, lab, jmp, mcp, serve]}
```

- Launched in a project, the server selects that project's `.venv`. `jumpstarter-mcp --env <environment>` (or
  `JMP_MCP_ENV`) selects another at the start; `jmp_use_environment` selects one later. With none, the server does
  the work itself, as it always did.
- `jmp_environments` lists the named ones and the Python environments near where the server was started (the active
  one, and the `.venv` of the working directory and of the directories above it).
- `jmp_connect(..., environment=...)` connects a device where its drivers are. **A connection belongs to the
  environment it was made in**: every later call that names its `connection_id` goes there, so devices leased from
  different environments and containers are driven side by side, and every answer says which environment it came
  from.
- A connection made where a device's drivers are not installed says which ones are missing, and how to choose the
  environment that has them.

### What an agent may choose

Using an environment starts a program, and the one who asks is a model: what it has read (a file of a repository,
the output of a device) may have told it what to ask. So an agent chooses among environments and never names one
into being:

- the environments of the environments file, and the one the server was started with (`--env`, `JMP_MCP_ENV`), are
  a person's own words: they may be used;
- so may the local one: the `.venv` of the very directory the server was launched in, and the environment that was
  active then. Launching an agent in a project is choosing that project's environment, and it is the one used when
  no other is selected. Launch an agent only in a project whose `.venv` you would run yourself;
- a Python environment that was only found further away (the `.venv` of a directory above) may be used only when
  the server was started with `--allow-found`. `jmp_environments` lists these apart, as found and not allowed;
- a path, a `container:` or an `image:` an agent gives that is none of these is refused, with what there is to
  choose from. Nothing is started for it and nothing is pulled.

The environments file is a list of commands to run, so it is read only when it belongs to whoever runs the server
and nobody else may write to it. The other server is started from a list of arguments, without a shell, and at most
eight run at once. A call that is passed on is written to the server's log by name, without its arguments.

A tool that fails, here or where it was passed on, fails in the protocol's own way (`isError`), so a host does not
take a refusal for an answer. Every tool says whether it only reads (`readOnlyHint`) and whether it may destroy
something (`destructiveHint`): `jmp_read` only reads, `jmp_call`, `jmp_run` and `jmp_delete_lease` may destroy. The
set of tools is fixed: it does not change with what is connected.

### Traces

The MCP library makes a span of every tool call and takes up the trace an agent's host sends with it
(`traceparent`), so what an agent does through this server is part of the agent's own trace. A call that is passed
on to another environment stays in that trace: the server there continues it. Spans say which tool was called on
which lease, exporter, driver and environment (`jumpstarter.*` attributes), and never the arguments.

Nothing is sent unless a person asks for it the standard way (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_EXPORTER`)
and the OpenTelemetry SDK is installed (`jumpstarter-mcp[otel]`), in each environment whose spans are wanted. A
container is passed the names of the `OTEL_*` variables that are set, not their values.

## API Reference

```{eval-rst}
.. automodule:: jumpstarter_mcp.server
   :members:
   :undoc-members:
```
