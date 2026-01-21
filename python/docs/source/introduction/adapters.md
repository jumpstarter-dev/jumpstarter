# Adapters

Jumpstarter uses adapters to transform network connections established by
drivers into different forms or interfaces that are more appropriate for
specific use cases.

## Architecture

Adapters in Jumpstarter follow a transformation pattern where:

- Adapters take a driver client as input
- They transform the connection into a different interface format
- The transformed interface is exposed to the user in a way that's tailored for
  specific scenarios

The architecture consists of these key components:

- **Adapter Base** - Adapters typically follow a context manager pattern using
  Python's `with` statement for resource management. Each adapter takes a driver
  client as input and transforms its connection.

- **Connection Transformation** - Adapters create a new interface on top of an
  existing driver connection, such as forwarding ports, providing web
  interfaces, or offering terminal-like access.

- **Resource Lifecycle** - Adapters handle proper setup and teardown of
  resources, ensuring connections are properly established and cleaned up.

Unlike [Drivers](drivers.md), which establish the foundational connections to
hardware or virtual interfaces, adapters focus on providing alternative ways to
interact with those connections without modifying the underlying drivers.
Adapters operate entirely on the client side and transform existing connections
rather than establishing new ones directly with hardware or virtual devices.

## Types

Different types of adapters serve different needs:

- **Port Forwarding Adapters** - Convert network connections to local ports or
  sockets
- **Interactive Adapters** - Provide interactive shells or console-like
  interfaces
- **Protocol Adapters** - Transform connections to use different protocols
  (e.g., SSH, VNC)
- **UI Adapters** - Create user interfaces for interacting with devices (e.g.,
  web-based VNC)

Adapters can be composed and extended for more complex scenarios:

- **Chaining adapters**: Use the output of one adapter as the input to another
- **Custom adapters**: Create specialized adapters for specific hardware or
  software interfaces
- **Extended functionality**: Add logging, monitoring, or security features on
  top of base adapters

## Implementation Patterns

Adapters typically implement the context manager protocol (`__enter__` and
`__exit__`) to ensure proper resource management. The general pattern is:

1. Initialize with a driver client reference
2. Set up the transformed connection in `__enter__`
3. Return the appropriate interface (URL, address, interactive object)
4. Clean up resources in `__exit__`

This allows adapters to be used in `with` statements for clean, deterministic
resource handling.

When working with adapters, follow these recommended practices:

1. **Always use context managers** (`with` statements) to ensure proper resource
   cleanup and prevent resource leaks
2. **Consider security implications** when forwarding ports or providing network
   access, especially when exposing services to external networks
3. **Implement proper error handling and retries** for robust connections in
   unstable network environments
4. **Use appropriate timeouts** to prevent hanging connections and ensure
   responsiveness
5. **Consider performance implications** for long-running connections or
   high-throughput scenarios, especially in resource-constrained environments

## Example Implementation

```{testcode}
from contextlib import contextmanager
import socket
import threading
from typing import Tuple, Any

class TcpPortforwardAdapter:
    """
    Adapter that forwards a remote TCP port to a local TCP port.

    Args:
        client: A network driver client that provides a connection
        local_host: Host to bind to (default: 127.0.0.1)
        local_port: Port to bind to (default: 0, which selects a random port)

    Returns:
        A tuple of (host, port) when used as a context manager
    """
    def __init__(self, client, local_host="127.0.0.1", local_port=0):
        self.client = client
        self.local_host = local_host
        self.local_port = local_port
        self._server = None
        self._thread = None

    def __enter__(self) -> Tuple[str, int]:
        # Create a socket server
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind((self.local_host, self.local_port))
        self._server.listen(5)

        # Get the actual port (if we used port 0)
        self.local_host, self.local_port = self._server.getsockname()

        # Start a thread to handle connections
        self._thread = threading.Thread(target=self._handle_connections, daemon=True)
        self._thread.start()

        return (self.local_host, self.local_port)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._server:
            self._server.close()
            self._server = None

        # Thread will exit because it's a daemon
        self._thread = None

    def _handle_connections(self):
        while True:
            try:
                client_socket, _ = self._server.accept()
                # For each connection, establish a connection to the remote
                # and set up bidirectional forwarding
                remote_conn = self.client.connect()
                self._start_forwarding(client_socket, remote_conn)
            except Exception:
                # Server was closed or other error
                break

    def _start_forwarding(self, local_socket, remote_conn):
        # Set up bidirectional forwarding between local_socket and remote_conn
        # Typically done with two threads, one for each direction
        # Implementation details depend on the specific driver client interface
        pass


# Example usage:
def example_usage():
    # Assuming 'client' is a network driver client
    with TcpPortforwardAdapter(client, local_port=8080) as (host, port):
        print(f"Service available at {host}:{port}")
        # The service is now accessible at the local address
        # while this context is active
```
