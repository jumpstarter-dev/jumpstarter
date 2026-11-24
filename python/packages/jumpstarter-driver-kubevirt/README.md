# jumpstarter-driver-kubevirt

Jumpstarter driver for managing KubeVirt virtual machines on remote clusters.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-kubevirt
```

## Drivers

- **KubevirtPower** — Start/stop VirtualMachines via KubeVirt API
- **KubevirtConsole** — Serial console via WebSocket subresource
- **KubevirtStorage** — Flash images via CDI DataVolumes
- **Kubevirt** — Composite root driver

The power, console and storage drivers take the VirtualMachine's `namespace`
and `vm_name`, and `kubeconfig`, the path to a kubeconfig for the cluster that
runs it. The console
authenticates with the bearer token in that kubeconfig; client certificates in
the kubeconfig are not used. The KubeVirt provisioner generates these drivers and their
configuration as `power`, `console` and, for DataVolume storage, `storage`.

`KubevirtStorage` supports local files and remote resources through the standard
Jumpstarter resource adapter:

```bash
j storage flash ./os.qcow2
j storage flash --target root:./os.qcow2 --target data:./dataset.raw
j power on
```

All selected disks are uploaded to new CDI DataVolumes before the driver stops
the VM and activates the replacements together. A failed upload preserves the
active disks. Successful flashes leave the VM off. An interrupted flash needs
no manual recovery: the next flash or the next lease cleans it up, and a second
simultaneous flash preempts the first.

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
for deployment requirements, storage configuration, and flashing behavior.
