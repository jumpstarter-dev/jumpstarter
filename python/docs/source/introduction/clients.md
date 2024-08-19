# Clients

To interact with your target device from a development machine or an automated
CI pipeline, Jumpstarter uses a `client`. The client can be used either as
a library through a testing tool such as [pytest](https://docs.pytest.org/en/stable/)
or through the [Jumpstarter CLI](../cli/index.md).

```{mermaid}
block-beta
  client
  space
  block:host
    exporter
  end
  space
  target["Target"]

  client-->host
  exporter-->target
```

## Local Client

When testing with hardware physically connected to your local machine or 
developing new drivers, it is recommended to use the local Jumpstarter client. 
This client be automatically used along with a transient exporter instance when 
using the [Jumpstarter Shell](../cli/shell.md).

## Remote Clients

When connecting to hardware that is not physically connected to your local 
machine, requests must be passed through a proxy server. Remote clients must be
configured with an endpoint and authentication token so they can communicate
with the server. To learn more about managing clients, see [Manage Clients](../cli/clients.md).

We will discuss how remote clients connect to exporters in the next section
on the [Jumpstarter Service](./service.md).
