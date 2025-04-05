# Adapters

Adapters are specialized components in Jumpstarter that transform network connections established by drivers into different forms or interfaces that are more appropriate for specific use cases.

## Adapter Architecture

Adapters in Jumpstarter follow a transformation pattern where:

- Adapters take a driver client as input
- They transform the connection into a different interface format
- The transformed interface is exposed to the user in a way that's tailored for specific scenarios

For comprehensive documentation on the adapter architecture, including detailed
patterns and examples, see the [Adapter Classes and Architecture](../api-reference/adapters.md) reference.

Unlike [Drivers](./drivers.md), which establish the foundational connections to hardware or virtual interfaces, adapters focus on providing alternative ways to interact with those connections without modifying the underlying drivers.

## Types of Adapters

```{include} ../api-reference/adapters/index.md
:start-after: "## Types of Adapters"
:end-before: "```{toctree}"
```

### Network Adapters

Jumpstarter includes several network adapters that transform network connections in useful ways:

- **TCP Port Forwarding** - Forward a remote TCP port to a local TCP port
- **Unix Port Forwarding** - Forward a remote Unix domain socket to a local socket
- **NoVNC** - Connect to a remote TCP port with a web-based VNC client
- **Pexpect** - Interact with a remote TCP port as if it's a serial console
- **Fabric** - Connect to a remote TCP port with the Fabric SSH client

For example, a network driver might establish a basic TCP connection to a device, while different
adapters could transform that connection into:
- A web-based VNC client interface for remote visual access
- A forwarded Unix socket for Unix domain socket-based applications
- A terminal-like interface for interactive command-line access
