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
