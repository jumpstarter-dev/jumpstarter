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