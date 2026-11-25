# Clients

To interact with your target device from a development machine or through a
CI/CD pipeline, you must use a Jumpstarter {term}`client`. The Python client can be used
either as a library or as a [CLI tool](../reference/man-pages/index.md).

## Types of Clients

Jumpstarter supports two types of clients: *local* and *remote*.

### Local Clients

When using Jumpstarter in {term}`local mode`, you can use the local client
functionality to directly access your hardware. `jmp shell --exporter` (or
`--exporter-config`) runs the {term}`exporter` described by that configuration
inside the `jmp` process and connects the local client to it over a Unix socket,
so there is no separate exporter service to start.

### Remote Clients

When using Jumpstarter in {term}`distributed mode`, the client must be configured to
connect to an instance of the {term}`service` that can authenticate and route requests
to the appropriate exporter instance.

The following parameters are required to set up a remote client:

- The URL of a service endpoint to connect to
- An authentication token, issued by the service or by an {term}`OIDC` identity
  provider it trusts (see
  [Authentication](../getting-started/configuration/authentication.md))

```{note}
The endpoint must be accessible from your client machine to communicate
with the service.
```

A remote client uses an exporter through a {term}`lease`: `jmp shell`
given a selector (`-l`) or an exporter name (`-n`) acquires one for the
duration of the shell, and `jmp create lease` creates one to use later, for
example with `jmp shell --lease`. The client holding a lease can share
it with other clients with `jmp share`, see
[Lease Sharing](../getting-started/guides/examples/lease-sharing.md). `jmp get`
lists exporters and leases, and `jmp describe` shows the details of
a single exporter, lease or {term}`client config`.
