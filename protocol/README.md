# Jumpstarter Protocol

The Jumpstarter Protocol defines the gRPC-based communication layer for the [Jumpstarter](https://jumpstarter.dev) Hardware-in-the-Loop (HiL) ecosystem. It enables seamless, secure, and scalable interaction between clients, the Jumpstarter Service, and exporters—whether they are interfacing with physical or virtual hardware, locally or remotely.

## Overview
Jumpstarter Protocol provides a unified gRPC interface for:

- **Clients** to control and monitor remote/local hardware
- **Exporters** to expose hardware interfaces over gRPC
- **Jumpstarter Service** to route and manage connections

Thanks to gRPC’s support for HTTP/2, streaming, and tunneling, the protocol works efficiently across enterprise networks, VPNs, and cloud environments. It appears as standard HTTPS traffic, making it compatible with existing security infrastructure.

## Features
- 🔌 **Unified Interface:** Interact with virtual or physical hardware through a consistent API.
- 🔐 **Secure by Design:** Leverages gRPC over HTTPS for encrypted communication.
- 🌐 **Flexible Topology:** Supports direct or routed connections via the Jumpstarter Router.
- 📡 **Tunneling Support:** Can tunnel Unix sockets, TCP, and UDP connections over gRPC streams.

## Related Projects

- [**Jumpstarter Python:**](https://github.com/jumpstarter-dev/jumpstarter) The Python implementation of this protocol for clients and exporters.
- [**Jumpstarter Service:**](https://github.com/jumpstarter-dev/jumpstarter-controller) The Go implementation of this protocol as a Kubernetes controller.


## Documentation

Jumpstarter's documentation is available at
[jumpstarter.dev](https://jumpstarter.dev).

## Contributing

Jumpstarter welcomes contributors of all levels of experience and would love to
see you involved in the project. See the [contributing
guide](https://jumpstarter.dev/contributing/) to get started.

## License

Jumpstarter is licensed under the Apache 2.0 License ([LICENSE](LICENSE) or
[https://www.apache.org/licenses/LICENSE-2.0](https://www.apache.org/licenses/LICENSE-2.0)).
