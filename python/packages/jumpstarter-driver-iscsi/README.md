# iSCSI Driver

`jumpstarter-driver-iscsi` provides a lightweight iSCSI **target** implementation powered by the Linux
LIO target framework via the
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
configures the target; LUNs are added at runtime with `add_lun`, `upload_image`
or `j iscsi serve`:

```yaml
export:
  iscsi:
    type: jumpstarter_driver_iscsi.driver.ISCSI
    config:
      root_dir: "/var/lib/iscsi"
      target_name: "demo"
      remove_created_on_close: false  # Keep disk images persistent (default)
      block_device_allowlist:         # Required to use is_block=True LUNs
        - /dev/sda
        - /dev/disk/by-id/my-disk
      # When size_mb is 0 a pre-existing file size is used.
```

### Config parameters

| Parameter               | Description                                                      | Type | Required | Default                       |
| ----------------------- | ---------------------------------------------------------------- | ---- | -------- | ----------------------------- |
| `root_dir`              | Directory where image files will be stored                      | str  | no       | `/var/lib/iscsi`              |
| `iqn_prefix`            | IQN prefix to use when building the target IQN                  | str  | no       | `iqn.2024-06.dev.jumpstarter` |
| `target_name`           | The target name appended to the IQN prefix                      | str  | no       | `target1`                     |
| `host`                  | IP address to bind the target to. Empty string will auto-detect | str  | no       | _auto_                        |
| `port`                  | TCP port the target listens on                                  | int  | no       | `3260`                        |
| `remove_created_on_close`| Automatically remove created files/directories when driver closes| bool | no       | false                         |
| `block_device_allowlist`| List of allowed block device paths for `is_block=True` LUNs. Symlinks are resolved before matching. Must be set to use block devices. | list[str] | no | `[]` (empty - block devices disabled) |

### File Management

The iSCSI server driver tracks the image files and directories written through its `storage` child during the session, such as uncompressed images uploaded with `upload_image`. Images that `upload_image` decompresses on the exporter (`.gz`, `.xz` and `.bz2` sources) and new backing files that `add_lun` creates are not tracked. By default, `remove_created_on_close` is set to `false` to preserve disk images that are typically reused across test sessions. Set to `true` if you want temporary disk images to be cleaned up automatically.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_iscsi.client.ISCSIServerClient()
    :members: start, stop, get_host, get_port, get_target_iqn, add_lun, remove_lun, list_luns, upload_image
```
