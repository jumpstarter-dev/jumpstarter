# iSCSI server driver

`jumpstarter-driver-iscsi` provides a lightweight iSCSI **target** implementation powered by the Linux
[RFC-tgt](https://github.com/open-iscsi/tcmu-runner/) framework via the
[`rtslib-fb`](https://github.com/open-iscsi/rtslib-fb) Python bindings.

> ⚠️  The driver **creates and manages an iSCSI _target_** (server).  To access the
> exported LUNs you still need a separate iSCSI **initiator** (client) on the
> machine running your test-code / DUT.

---

## Installation

`rtslib-fb` relies on the in-kernel LIO target framework which is packaged
differently by each distribution.  **You should be able to run `sudo targetcli`
without errors before you start the Jumpstarter driver.**

Fedora:

```{code-block} console
$ sudo dnf install targetcli python3-rtslib
```

Finally, install the driver itself from the Jumpstarter package index:

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-iscsi
```

## Configuration

The driver is configured through the exporter YAML file.  A minimal example
exports the local file `disk.img` as a 5 GiB LUN:

```yaml
export:
  iscsi:
    type: jumpstarter_driver_iscsi.driver.ISCSI
    config:
      root_dir: "/var/lib/iscsi"
      target_name: "demo"
      # When size_mb is 0 a pre-existing file size is used.
```

### Config parameters

| Parameter   | Description                                                                  | Type | Required | Default                           |
| ----------- | ---------------------------------------------------------------------------- | ---- | -------- | --------------------------------- |
| `root_dir`  | Directory where image files will be stored.                                 | str  | no       | `/var/lib/iscsi`                  |
| `iqn_prefix`| IQN prefix to use when building the target IQN.                              | str  | no       | `iqn.2024-06.dev.jumpstarter`     |
| `target_name`| The target name appended to the IQN prefix.                                 | str  | no       | `target1`                         |
| `host`      | IP address to bind the target to.  Empty string will auto-detect default IP. | str  | no       | *auto*                            |
| `port`      | TCP port the target listens on.                                              | int  | no       | `3260`                            |

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_iscsi.client.ISCSIServerClient()
    :members: start, stop, get_host, get_port, get_target_iqn, add_lun, remove_lun, list_luns, upload_image
```

## Usage examples

### Start a server and export a raw image
```{testcode}
from jumpstarter_driver_iscsi.driver import ISCSI
from jumpstarter.common.utils import serve

# Bring up a server instance that stores its images in the current directory.
instance = serve(ISCSI(root_dir="./images", target_name="demo"))
iscsi = instance.__enter__()

# Upload an existing QCOW2 image and expose it as LUN 0.
iscsi.upload_image("demo", "./disk.qcow2")
print("Target IQN:", iscsi.get_target_iqn())
```

### Manipulate LUNs after the server is already running
```{testcode}
iscsi.add_lun("scratch", "scratch.img", size_mb=1024)  # 1 GiB new image
for lun in iscsi.list_luns():
    print(lun)

iscsi.remove_lun("scratch")
```

```{testcleanup} *
instance.__exit__(None, None, None)
```
