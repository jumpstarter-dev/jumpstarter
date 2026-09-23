# Off-Cluster QEMU Provisioner

The `qemu-ssh.jumpstarter.dev` provisioner runs QEMU virtual targets on remote
lab hosts outside the Kubernetes cluster. It connects to hosts via SSH and
deploys containers using Podman quadlets, giving you the same container-based
exporter + runtime pattern as in-cluster QEMU but on dedicated hardware with
direct KVM access.

**When to use this provisioner:**

- Your lab hosts have KVM-capable hardware or GPU passthrough not available
  in the cluster
- You need bare-metal performance for emulation (e.g., automotive SoC targets)
- You want to scale virtual target pools across multiple lab machines while
  keeping orchestration centralized in Kubernetes

## Prerequisites

### Remote lab hosts

Each remote host must have:

- **Podman** installed (for running containers)
- **systemd** (for quadlet-based container lifecycle)
- **SSH access** with key-based authentication
- (Optional) **KVM** support (`/dev/kvm` present) for hardware-accelerated
  emulation

### Kubernetes cluster

The cluster must have:

- Jumpstarter operator installed
- The `qemu-ssh.jumpstarter.dev` provisioner enabled in the Jumpstarter CR

## Step 1: Enable the provisioner

Add `qemu-ssh.jumpstarter.dev` to the `exporterSets.provisioners` list in your
Jumpstarter CR:

```yaml
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: jumpstarter
spec:
  exporterSets:
    image: quay.io/jumpstarter-dev/exporter-set-controller:latest
    provisioners:
      - name: qemu.jumpstarter.dev
        enabled: true
      - name: qemu-ssh.jumpstarter.dev
        enabled: true
```

The operator creates a Deployment for the `qemu-ssh` controller automatically.

## Step 2: Create SSH credentials

Create a Kubernetes Secret containing the SSH private key used to connect to
your lab hosts:

```bash
kubectl create secret generic lab-ssh-key \
  --from-file=ssh-privatekey=$HOME/.ssh/id_ed25519 \
  -n jumpstarter
```

The Secret uses the standard `kubernetes.io/ssh-auth` type. The key must be in
the `ssh-privatekey` field.

## Step 3: Create a VirtualTargetClass

The `VirtualTargetClass` defines the pool profile — which hosts to use, SSH
configuration, and default resource allocations:

```yaml
apiVersion: virtualtarget.jumpstarter.dev/v1alpha1
kind: VirtualTargetClass
metadata:
  name: qemu-ssh-aarch64
  namespace: jumpstarter
spec:
  provisioner: qemu-ssh.jumpstarter.dev
  credentialsSecretRef:
    name: lab-ssh-key
  bindingMode: Immediate
  reclaimPolicy: Delete
  parameters:
    ssh:
      user: root
      port: 22
    hosts:
      - name: lab-host-01.example.com
        arch: aarch64
        slots: 2
      - name: lab-host-02.example.com
        arch: aarch64
        slots: 2
    runtime:
      kvm: true
    arch: aarch64
    resources:
      cpu: 4
      memory: 4Gi
    storage:
      size: 16Gi
```

### Parameters reference

| Parameter | Description |
|-----------|-------------|
| `ssh.user` | Default SSH username (fallback: `root`) |
| `ssh.port` | Default SSH port (fallback: `22`) |
| `hosts[].name` | FQDN or IP of the remote host |
| `hosts[].arch` | CPU architecture (informational) |
| `hosts[].slots` | Maximum concurrent instances on this host |
| `hosts[].user` | Per-host SSH user override |
| `hosts[].port` | Per-host SSH port override |
| `runtime.kvm` | Pass `/dev/kvm` to the runtime container |
| `runtime.devices` | Additional devices to pass through (list of paths) |
| `arch` | Default QEMU architecture |
| `resources.cpu` | Default vCPU count |
| `resources.memory` | Default memory (e.g., `4Gi`) |
| `storage.size` | Default disk size (e.g., `16Gi`) |

## Step 4: Create an ExporterSet

The `ExporterSet` manages the scaling pool. It references the
`VirtualTargetClass` and can override parameters:

```yaml
apiVersion: virtualtarget.jumpstarter.dev/v1alpha1
kind: ExporterSet
metadata:
  name: aarch64-ssh-pool
  namespace: jumpstarter
spec:
  minReplicas: 0
  maxReplicas: 4
  minAvailableReplicas: 1
  scaleDownCooldown: 5m
  recycleStrategy: ExitAndReplace
  virtualTargetClassName: qemu-ssh-aarch64
  selector:
    matchLabels:
      board: aarch64-qemu
      virtual: "true"
  template:
    metadata:
      labels:
        board: aarch64-qemu
        arch: aarch64
        virtual: "true"
    spec:
      drivers:
        - name: qemu
          type: jumpstarter_driver_qemu.driver.Qemu
        - name: power
          type: jumpstarter_driver_power.driver.QemuPower
        - name: serial
          type: jumpstarter_driver_serial.driver.QemuSerial
```

The `tcp` driver is auto-injected by the provisioner (SSH host-forwarding on
port 2222).

## How it works

When the ExporterSet scales up:

1. The controller creates an `Exporter` CR (cluster-side)
2. Once credentials are ready, it selects a remote host with available capacity
3. Connects to the host via SSH
4. Writes the exporter configuration YAML to `/etc/jumpstarter/exporters/`
5. Creates Podman quadlet `.container` files under `/etc/containers/systemd/`
   for both the **runtime** (QEMU) and **exporter** containers
6. Creates a shared Podman volume for inter-container communication
   (QEMU sockets)
7. Reloads systemd and starts the containers
8. Annotates the Exporter CR with the host assignment

When scaling down or cleaning up:

1. Stops and disables the systemd services
2. Removes the quadlet files and exporter config
3. Removes the shared Podman volume
4. Reloads systemd
5. Deletes the Exporter CR

### Container layout on the remote host

Each exporter instance creates two containers:

- **`<name>-runtime`**: Runs the QEMU emulator with access to `/dev/kvm`
  (if enabled) and the shared volume for communication sockets
- **`<name>-exporter`**: Runs the Jumpstarter exporter, connecting back to
  the cluster controller and mounting the shared volume

Both containers communicate via Unix sockets on the shared Podman volume
(`/shared/launcher.sock`).

## Leasing targets

Users lease off-cluster targets the same way as any other target — the
placement is transparent:

```bash
jmp lease -l board=aarch64-qemu,virtual=true
```

## Troubleshooting

### Check exporter-set controller logs

```bash
kubectl logs -l component=exporterset-controller,provisioner=qemu-ssh-jumpstarter-dev \
  -n jumpstarter -f
```

### Check remote host containers

SSH into the lab host and inspect the Podman containers:

```bash
podman ps -a --filter "label=managed-by=jumpstarter"
systemctl status *-runtime *-exporter
journalctl -u <name>-runtime -u <name>-exporter
```

### Check quadlet files

```bash
ls /etc/containers/systemd/*.container
cat /etc/jumpstarter/exporters/*.yaml
```

### Common issues

- **SSH connection failures**: Verify the SSH key in the Secret matches
  `authorized_keys` on the remote hosts
- **KVM not available**: Ensure `/dev/kvm` exists on the host and the
  `runtime.kvm` parameter is set
- **Containers not starting**: Check `podman logs <name>-runtime` and
  `podman logs <name>-exporter` on the remote host
- **All slots full**: Increase `hosts[].slots` or add more hosts to the
  VirtualTargetClass parameters
