# Drivers

```{warning}
This project is still evolving, so these docs may be incomplete or out-of-date.
```

Jumpstarter uses Python modules called drivers to interact with the hardware
interfaces connected to the Device Under Test (DUT).

Similar to the drivers used by your operating system, drivers in Jumpstarter
enable us to interact with hardware and provide abstractions that make it
easier to use.

Drivers in Jumpstarter consist of three components:

- `Driver` - Implements the logic to configure and use the interface(s) provided
by the host system. For instance, a TCP port.
- `DriverInterface` - Optionally defines the contract between the driver client and the
driver itself, so multiple drivers can share the same client.
- `DriverClient` - Provides a user-friendly interface that can be used by clients
to interact with the underlying `Driver` either locally or remotely over the network.

Drivers follow a client/server model similar to the client and exporter.
The exporter instance runs the `Driver` itself to interact with the hardware.
Clients use a `DriverClient` class to interact with the driver through the
contract defined in the `DriverInterface`.

When a client requests a lease and  connects to the exporter, a session is created
for all the tests the client has to execute. Within the session, the specified `Driver`
subclass is instantiated for each of the configured interfaces defined in the
exporter configuration. These driver instances live throughout the duration of
the session and can run setup/teardown logic and keep state internally for that
session.

On the client side, a `DriverClient` subclass is instantiated for each interface
that is exported by the exporter. The `DriverClient` is used to interact with
each `Driver` through the defined `DriverInterface` for each driver. As the
client may not be on the same machine as the exporter, the `DriverClient`
classes are loaded dynamically if they are specified in the list of allowed
packages.

To keep the client/server library versions in sync, it is recommended not to make
breaking changes to the `DriverInterface`. Only add new methods when necessary and
avoid changing the method signatures. If breaking changes are required, new
`MyDriverInterfaceV2`, `MyDriverClientV2`, and `MyDriverV2` classes can be created
within the same Python module.
