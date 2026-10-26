# jumpstarter-driver-kubevirt

Jumpstarter driver for managing KubeVirt virtual machines on remote clusters.

## Drivers

- **KubevirtPower** — Start/stop VirtualMachines via KubeVirt API
- **KubevirtConsole** — Serial console via WebSocket subresource
- **KubevirtStorage** — Flash images via CDI DataVolumes
- **Kubevirt** — Composite root driver

`KubevirtStorage` supports local files and remote resources through the standard
Jumpstarter resource adapter:

```bash
j storage flash ./os.qcow2
j storage flash --target root:./os.qcow2 --target data:./dataset.raw
j power on
```

All selected disks are uploaded to new CDI DataVolumes before the driver stops
the VM and activates the replacements together. A failed upload preserves the
active disks. Successful flashes leave the VM off. `j storage recover` discards
an interrupted upload or finishes cleanup after a committed activation.

Configure `upload_proxy_url` with an HTTPS CDI upload proxy reachable from the
exporter, and optionally `upload_ca` with a PEM CA bundle. `disks` maps names to
`size`, `storage_class_name`, `access_modes`, and `volume_mode`. The KubeVirt
provisioner injects these settings from `storage.disks` automatically. `root`
is the default target; other names must match disks already present in the VM.
Flashing requires DataVolume-backed disks and an Immediate storage class.

The implementation uses CDI's asynchronous upload endpoint and waits for the
DataVolume's `Succeeded` phase before activation. See the
[CDI upload protocol](https://github.com/kubevirt/containerized-data-importer/blob/main/doc/upload.md)
and the [provisioner guide](https://jumpstarter.dev/main/getting-started/guides/kubevirt-provisioner.html)
for deployment requirements, storage configuration, and recovery behavior.
