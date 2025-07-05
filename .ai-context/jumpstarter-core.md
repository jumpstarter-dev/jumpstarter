# Jumpstarter Hardware Testing Framework

## Overview

Jumpstarter is an open-source hardware testing framework using a client/server architecture over gRPC.

## Architecture

- **Drivers**: Provide abstractions for hardware interfaces (power, network, serial)
- **Driver Clients**: Provide a Python API or optional CLI interface for drivers
- **Exporters**: Expose drivers via gRPC connections
- **Clients**: Connect to exporters remotely or locally for device control
- **Service**: Kubernetes controller and router for distributed mode
