# probe-rs driver

`jumpstarter-driver-probe-rs` provides functionality for remote debugging and
flashing of embedded devices using the [probe-rs](https://probe.rs) tools.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-probe-rs
```

## Configuration

Example configuration:

```yaml
export:
  probe:
    type: jumpstarter_driver_probe_rs.driver.ProbeRs
    config:
      probe: "2e8a:000c:5798DE5E500ACB60"
      probe_rs_path: "/home/majopela/.cargo/bin/probe-rs"
      chip: "RP2350"
      protocol: "swd"
      connect_under_reset: false
```

### Config parameters

| Parameter           | Description                                                    | Type            | Required | Default  |
| ------------------- | -------------------------------------------------------------- | --------------- | -------- | -------- |
| probe               | The probe id, can be in VID:PID format or VID:PID:SERIALNUMBER | str             | no       |          |
| probe_rs_path       | The path to the probe-rs binary                                | str             | no       | probe-rs |
| chip                | The target chip                                                | str             | no       |          |
| protocol            | The target protocol                                            | "swd" or "jtag" | no       |          |
| connect_under_reset | Connect to the target while asserting reset                    | bool            | no       | false    |

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_probe_rs.client.ProbeRsClient()
    :members:
```

### CLI

The probe driver client comes with a CLI tool that can be used to interact with
the target device.
```
jumpstarter ⚡ local ➤ j probe
Usage: j probe [OPTIONS] COMMAND [ARGS]...

  probe-rs client

Options:
  --help  Show this message and exit.

Commands:
  download  Download a file to the target
  erase     Erase the target, this is a slow operation.
  info      Get target information
  read      read from target memory
  reset     Reset the target
```
