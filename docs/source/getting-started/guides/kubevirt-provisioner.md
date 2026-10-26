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
  recycleStrategy: ExitAndReplace
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
| `storage.source.http.url` | string | | HTTP URL for disk image |
| `storage.source.registry.image` | string | | Container registry image |
| `storage.source.pvc.name` | string | | Clone from existing PVC |
| `storage.source.pvc.namespace` | string | | PVC namespace |

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
```

## Lifecycle

The exporter runs as the main container in its management-cluster pod. The
ExporterSet's `recycleStrategy` controls what happens when a lease ends:

| Strategy | Lease release | Exporter crash |
|----------|---------------|----------------|
| `ExitAndReplace` (default) | The exporter exits, its pod completes, and the controller retires the old Exporter and provisions a replacement as needed by the pool. | The pod fails without restarting. The controller preserves the VM while the lease is active and recycles it after the lease ends. |
| `InPlaceReuse` | The exporter keeps running and the next lease uses the same VM and disks. | Kubernetes restarts the exporter in the same pod, preserving the VM and disks. |

When an Exporter is retired, the VirtualTargetClass's `reclaimPolicy` determines
what happens to its remote resources:

- `Delete`: The provisioner deletes the VM and its Services. KubeVirt/CDI
  garbage collection removes the VM-owned DataVolumes and PVCs. A replacement
  gets a new Exporter and VM identity.
- `Retain`: The VM, disks, and Services remain for manual recovery or cleanup.
  A replacement still gets a new identity; it does not reuse the retained VM.

Remote cleanup errors leave the old Exporter in place so the controller can
retry. Active leases prevent retirement under either reclaim policy.

### Upgrading existing exporter pods

Pods created by older controllers run the exporter as a restarting sidecar
alongside `keepalive`. Upgrading the controller does not change these existing
pods. After upgrading, disable each old Exporter to drain it:

```bash
kubectl -n <management-namespace> patch exporter <old-exporter> \
  --type merge -p '{"spec":{"enabled":false}}'
```

The controller waits for any active lease to end, cleans up according to the
reclaim policy, and refills the pool with the new pod layout. Deleting only the
pod preserves the Exporter and its existing VM.

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
