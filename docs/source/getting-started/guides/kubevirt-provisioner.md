# KubeVirt Provisioner

The `kubevirt.jumpstarter.dev` provisioner creates virtual machines on a remote
[KubeVirt](https://kubevirt.io/) / [OpenShift Virtualization](https://docs.openshift.com/container-platform/latest/virt/about_virt/about-virt.html)
cluster and manages them through the Jumpstarter {term}`ExporterSet` lifecycle.

This guide walks through configuring a two-cluster setup where:

- A **management cluster** (e.g. kind) runs the Jumpstarter operator and
  lightweight exporter pods.
- A **target cluster** (e.g. OpenShift with CNV) runs the actual VMs.

## Prerequisites

- Jumpstarter operator installed on the management cluster
- A KubeVirt-capable target cluster (OpenShift Virtualization or upstream
  KubeVirt)
- A kubeconfig for the target cluster with permissions to create
  `VirtualMachine`, `Service`, and `DataVolume` resources
- The `jumpstarter-kubevirt` exporter image available to the management cluster

For flashing, CDI must be installed and its HTTPS upload proxy must be reachable
from the exporter pod on the management cluster. Configure `storage.uploadProxyURL`
with the proxy's base URL. Use `storage.uploadProxyCA` for a PEM CA bundle when the
proxy certificate is not signed by a publicly trusted CA.

The target kubeconfig also needs `get` and `patch` on VirtualMachines, `get` on
VirtualMachineInstances and PVCs, `get`, `create`, and `delete` on DataVolumes,
and `create` on `uploadtokenrequests.upload.cdi.kubevirt.io` in the VM namespace.
It needs cluster-scoped `get` on the StorageClasses used by the disks.

Flashing currently requires an `Immediate` storage class with storage accessible
to the VM. `WaitForFirstConsumer` is rejected before allocating replacement disks;
the driver does not force binding. Allow quota for both the current and replacement
disks, and for CDI scratch space during conversion.

## Architecture

```
┌──────────────────────────┐       ┌──────────────────────────────┐
│   Management Cluster     │       │   Target Cluster (CNV)       │
│                          │       │                              │
│  ExporterSet controller  │──────▶│  VirtualMachine CR           │
│  Exporter Pod            │       │  DataVolume (disk image)     │
│    ├─ KubevirtPower      │──────▶│  SSH Service (NodePort/LB)   │
│    ├─ KubevirtConsole    │       │  Forward Port Services       │
│    ├─ KubevirtStorage    │       │                              │
│    ├─ ssh (TcpNetwork)   │       │                              │
│    └─ custom (TcpNetwork)│       │                              │
└──────────────────────────┘       └──────────────────────────────┘
```

The provisioner creates VMs with `RunStrategy: Halted` — the Python
`KubevirtPower` driver's `power.on()` starts the VM.

## Setup

### 1. Create a credentials Secret

The provisioner needs a kubeconfig to reach the target cluster. Create a Secret
in the management cluster's namespace:

```bash
kubectl create secret generic kubevirt-credentials \
  --from-file=kubeconfig=/path/to/target-kubeconfig \
  -n jumpstarter-lab
```

### 2. Create a VirtualTargetClass

The {term}`VirtualTargetClass` defines the provisioner backend and shared
parameters for all VMs in the pool.

#### Minimal example (containerDisk)

```yaml
apiVersion: virtualtarget.jumpstarter.dev/v1alpha1
kind: VirtualTargetClass
metadata:
  name: kubevirt-basic
  namespace: jumpstarter-lab
spec:
  provisioner: kubevirt.jumpstarter.dev
  bindingMode: Immediate
  reclaimPolicy: Delete
  credentialsSecretRef:
    name: kubevirt-credentials
  images:
    exporter:
      image: quay.io/jumpstarter-dev/jumpstarter-kubevirt:latest
  parameters:
    namespace: my-vms
    image: quay.io/containerdisks/fedora:latest
```

#### Full example (DataVolume + SSH + port forwarding + cloud-init)

```yaml
apiVersion: virtualtarget.jumpstarter.dev/v1alpha1
kind: VirtualTargetClass
metadata:
  name: kubevirt-full
  namespace: jumpstarter-lab
spec:
  provisioner: kubevirt.jumpstarter.dev
  bindingMode: Immediate
  reclaimPolicy: Delete
  credentialsSecretRef:
    name: kubevirt-credentials
  images:
    exporter:
      image: quay.io/jumpstarter-dev/jumpstarter-kubevirt:latest
  parameters:
    namespace: my-vms
    resources:
      cpu: 4
      memory: "8Gi"
      storage: "40Gi"
    sshAccess: LoadBalancer
    forwardPorts:
      adb: 6520
      web: 8080
    cloudInit: |
      #cloud-config
      ssh_pwauth: false
      ssh_authorized_keys:
        - "ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY"
      runcmd:
        - apt-get update
        - apt-get install -y docker.io
    storage:
      type: dataVolume
      uploadProxyURL: "https://cdi-uploadproxy.apps.example.com"
      size: "40Gi"
      source:
        http:
          url: "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img"
```

### 3. Create an ExporterSet

The {term}`ExporterSet` references the VirtualTargetClass and defines how many
replicas (VMs) to maintain:

```yaml
apiVersion: virtualtarget.jumpstarter.dev/v1alpha1
kind: ExporterSet
metadata:
  name: kubevirt-pool
  namespace: jumpstarter-lab
spec:
  minReplicas: 1
  maxReplicas: 3
  selector:
    matchLabels:
      app: kubevirt-pool
  template:
    metadata:
      labels:
        app: kubevirt-pool
    spec:
      drivers: []
  virtualTargetClassName: kubevirt-full
```

## Parameter Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `namespace` | string | *required* | Namespace on the target cluster for VMs |
| `image` | string | *required for containerDisk* | Container disk image |
| `resources.cpu` | int | `2` | vCPU cores |
| `resources.memory` | string | `"4Gi"` | Guest memory |
| `resources.storage` | string | `"20Gi"` | Root disk size |
| `sshAccess` | string | `""` | `"NodePort"` or `"LoadBalancer"` to expose SSH |
| `forwardPorts` | map | `{}` | Named ports to forward (e.g. `adb: 6520`) |
| `cloudInit` | string | `""` | Cloud-init userdata |
| `storage.type` | string | `"containerDisk"` | `"containerDisk"` or `"dataVolume"` |
| `storage.size` | string | `"20Gi"` | DataVolume disk size |
| `storage.storageClassName` | string | `""` | Storage class for the DataVolume |
| `storage.accessModes` | list | `["ReadWriteOnce"]` | PV access modes |
| `storage.volumeMode` | string | StorageProfile default | `Block` or `Filesystem` |
| `storage.uploadProxyURL` | string | | HTTPS CDI upload proxy base URL; required for flashing |
| `storage.uploadProxyCA` | string | System trust store | PEM CA bundle for the upload proxy |
| `storage.disks` | map | Single `root` disk | Named disk definitions; requires `dataVolume` storage and a `root` entry |
| `storage.disks.<name>.size` | string | `storage.size` | Disk capacity, including the expanded size of an uploaded image |
| `storage.disks.<name>.bootOrder` | integer | `1` for `root` | Positive, unique boot priority |
| `storage.disks.<name>.source` | object | `{blank: {}}` | Initial image: HTTP, registry, cloned PVC, or blank disk |
| `storage.source.http.url` | string | | HTTP URL for disk image |
| `storage.source.registry.image` | string | | Container registry image |
| `storage.source.pvc.name` | string | | Clone from existing PVC |
| `storage.source.pvc.namespace` | string | | PVC namespace |

Named disks inherit `size`, `storageClassName`, `accessModes`, and `volumeMode`
from `storage`; each disk may override these settings. Without `storage.disks`,
the existing `storage.size` and `storage.source` settings describe `root`.
An omitted source creates a blank disk. With named disks, put the root source
under `storage.disks.root.source` instead of `storage.source`.

For example, a boot disk and a separate data disk:

```yaml
storage:
  type: dataVolume
  storageClassName: vm-disks
  uploadProxyURL: "https://cdi-uploadproxy.apps.example.com"
  disks:
    root:
      size: "30Gi"
      source:
        registry:
          image: quay.io/containerdisks/fedora:latest
    data:
      size: "50Gi"
      source:
        blank: {}
```

Disks have stable names and serial numbers and use the virtio bus. `cloudinit` is
reserved. The provisioner creates standalone DataVolumes owned by the remote VM;
source PVCs are cloned, and are not adopted or deleted.

## Auto-Injected Drivers

The provisioner automatically injects the following drivers into the exporter
configuration:

| Driver | Type | Purpose |
|--------|------|---------|
| `power` | `KubevirtPower` | Start/stop/status via KubeVirt API |
| `console` | `KubevirtConsole` | Serial console via WebSocket |
| `storage` | `KubevirtStorage` | Flash disk images via DataVolume |
| `ssh` | `TcpNetwork` | SSH connectivity (when `sshAccess` is set) |
| `adb` | `AdbServer` | ADB tunneling (when `forwardPorts` has `adb`) |
| `rootcanal` | `RootCanal` | Bluetooth simulation via RootCanal test channel (when `forwardPorts` has `rootcanal`) |
| `netsim` | `Netsim` | Wireless simulation via netsim REST API (when `forwardPorts` has `netsim`) |
| *custom* | `TcpNetwork` | Other `forwardPorts` entries |

All KubeVirt drivers receive the remote cluster's kubeconfig automatically.

## Usage

### Leasing an exporter

```bash
jmp shell -l app=kubevirt-pool
```

### Power management

```bash
j power on
j power read    # Running / Halted
j power off
```

### Serial console

```bash
j console pipe
```

### SSH (when sshAccess is configured)

The `ssh` TcpNetwork driver provides raw TCP access to port 22. Use it with
`PexpectAdapter` or connect directly via the resolved Service address.

### Storage flashing

Replace the VM's root disk with a new image:

```bash
j storage flash /path/to/new-image.qcow2
j power on
```

Flash a named data disk, or replace several disks together:

```bash
j storage flash --target data:/path/to/dataset.raw
j storage flash --target root:/path/to/os.qcow2 --target data:/path/to/dataset.raw
```

A positional file always targets `root`. Targets are whole disks, not partitions.
Unspecified disks keep their contents. Files and HTTP(S) URLs use the standard
Jumpstarter resource transfer path; the exporter streams them to CDI without
storing a full image locally. CDI supports raw and qcow2 images, including its
supported compressed image formats. `containerDisk` volumes cannot be flashed;
use `storage.type: dataVolume`.

The command uploads every replacement to a new DataVolume and waits for CDI
processing to finish. It then stops the VM, waits for the VMI to disappear, and
switches all selected disk references in one conditional update. A successful
flash leaves the VM off. It does not verify that the new OS boots. Disk contents
persist across power cycles and exporter restarts and follow the VM's reclaim
policy.

An upload or conversion failure preserves the current disks and leaves a running
VM running. A shutdown failure also preserves the current disks, but the VM may
remain off. Power changes and another flash are rejected while a flash is pending.

After a disconnected client or exporter restart, recover an unfinished operation:

```bash
j storage recover
```

Recovery discards uncommitted replacement volumes. If activation already
succeeded, it preserves the new disks and retries cleanup of the old ones.
Cleanup failure does not roll back an activated image. The operation record is
stored on the VM, so recovery does not depend on the exporter pod's filesystem.
Use recovery after the original flash has stopped; within an exporter, it waits
for an active upload or commit call to finish. A subsequent flash gets a new
operation ID and fresh volumes; interrupted uploads are not resumed.

## Lifecycle

- **Create**: ExporterSet controller → provisioner creates VM + Services on
  target cluster → exporter pod starts on management cluster
- **Running**: Exporter pod uses kubeconfig to control VM via KubeVirt API
- **Delete**: `reclaimPolicy: Delete` → provisioner deletes VM and Services on
  target cluster. Services also have `ownerReferences` to the VM as a
  secondary cleanup mechanism.

## Networking

### SSH Access

When `sshAccess` is set to `NodePort` or `LoadBalancer`, the provisioner:

1. Adds port 22 to the VMI interface
2. Creates a Service on the target cluster selecting the VM's pod
3. Waits for a reachable Service address (LoadBalancer ingress or a cluster
   node address for NodePort) and injects a `TcpNetwork` driver with the
   resolved host and port

### Port Forwarding

Each entry in `forwardPorts` follows the same pattern — a port is added to the
VMI interface, a Service is created, and a `TcpNetwork` driver is injected with
the resolved address.

## Troubleshooting

### VM not starting

Check the VirtualMachine status on the target cluster:

```bash
oc get vm -n <namespace>
oc describe vm <vm-name> -n <namespace>
```

### DataVolume stuck importing

Check the DataVolume and CDI importer pod:

```bash
oc get dv -n <namespace>
oc get pods -n <namespace> | grep importer
```

### Exporter pod not connecting

Verify the credentials Secret contains a valid kubeconfig:

```bash
kubectl get secret kubevirt-credentials -n jumpstarter-lab -o jsonpath='{.data.kubeconfig}' | base64 -d | head -5
```

### Service not reachable

Check the Service and endpoints on the target cluster:

```bash
oc get svc -l jumpstarter.dev/provisioner=kubevirt.jumpstarter.dev -n <namespace>
oc get endpoints <svc-name> -n <namespace>
```
