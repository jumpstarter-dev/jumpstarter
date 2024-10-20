# Exporters

To enable access to your target device, Jumpstarter uses a component called
the `exporter`, which runs on a host system connected directly to your hardware.
We call it an exporter because it "exports" the interfaces connected to the 
target.

## Hosts

Typically, the exporter will run on a low-power host systems such as a 
Raspberry Pi or mini PC with sufficient interfaces to connect to your hardware.
If the host has sufficient interfaces, it may be connected to multiple targets,
however, each target requires its own exporter instance.

```{mermaid}
block-beta
  block:host
    exporter
  end
  
  space

  target["Target Device"]

  exporter-->target
```

## Drivers

Exporters use modules called `drivers`, which define how to interact with
a specific interface (e.g. USB, Serial, CAN, etc.). Each driver provides a
method to interact with and/or tunnel an interface connected to the target. 

```{mermaid}
block-beta
  block:host
    columns 1
    exporter
    block:drivers
        usb["USB"]
        serial["Serial"]
        can["CAN"]
        etc["etc."]
    end
  end
  space
  target["Target Device"]

  host-->target
```

While Jumpstarter comes with drivers for many basic interfaces, custom drivers
can also be developed for specialized hardware/interfaces or to provide
domain-specific abstractions for your use case.

## Composite drivers

Multiple drivers can be combined to create a `composite` driver with additional
device-specific functionality for your use case. For example, you may want to
develop a composite driver that provides methods that simulate the physical wiring
harness your device will use in production.
