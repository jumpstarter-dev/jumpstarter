# Jumpstarter Protocol

The Jumpstarter Protocol defines the gRPC-based communication layer for the
[Jumpstarter](https://jumpstarter.dev) Hardware-in-the-Loop (HiL) ecosystem. It
enables seamless, secure, and scalable interaction between clients, the
Jumpstarter Service, and exporters -- whether they are interfacing with physical
or virtual hardware, locally or remotely.

The protocol provides a unified gRPC interface for clients to control and monitor
hardware, exporters to expose hardware interfaces, and the Jumpstarter Service to
route and manage connections. Thanks to gRPC's support for HTTP/2, streaming, and
tunneling, the protocol works efficiently across enterprise networks, VPNs, and
cloud environments.

## Code Generation

The protobuf definitions live under `proto/`. Downstream consumers generate
language-specific bindings using [Buf](https://buf.build/). Both the controller
(Go) and the Python packages maintain their own `buf.gen.yaml` to generate stubs
from these definitions.

## Development

```sh
make lint
```
