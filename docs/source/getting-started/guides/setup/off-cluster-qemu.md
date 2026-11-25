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
- **SSH access** as `root` with key-based authentication: the provisioner
  writes under `/etc` over SFTP and runs `systemctl` and rootful `podman`
  without `sudo`. It does not verify the host's SSH key.
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
    image: quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest
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

The key must be in the `ssh-privatekey` field, the field name of the standard
`kubernetes.io/ssh-auth` type; the provisioner does not check the Secret type.
The SSH user and port are set with `host.user` and `host.port` in the
VirtualTargetClass or ExporterSet parameters (not in the Secret).

## Step 3: Create a VirtualTargetClass

The `VirtualTargetClass` defines the pool profile - which host to use, SSH
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
    host:
      name: lab-host-01.example.com
      user: root
      port: 22
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
| `host.name` | FQDN or IP of the remote host (required) |
| `host.user` | SSH username (default: `root`) |
| `host.port` | SSH port (default: `22`) |
| `runtime.kvm` | Pass `/dev/kvm` to the runtime container |
| `runtime.devices` | Additional devices to pass through (list of `/dev/` paths) |
| `runtime.host_network` | Run both containers on the host network instead of Podman's bridge (default `false`). Needed when the exporter must reach a service on the host's own `127.0.0.1`; only one instance per host can then use a given forwarded port |
| `arch` | Default QEMU architecture |
| `resources.cpu` | Default vCPU count |
| `resources.memory` | Default memory (e.g., `4Gi`) |
| `storage.size` | Default disk size (e.g., `16Gi`) |

## Step 4: Create an ExporterSet

Each ExporterSet manages instances on one host. The `maxReplicas` field
controls how many concurrent instances run on that host. To use multiple
hosts, create additional ExporterSets that reference the same
VirtualTargetClass but override `parameters.host`:

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
```

By default the provisioner auto-aliases the QEMU children as root-level refs 
`power`, `storage`, and `serial` so clients can
use `client.power` like on real hardware. Set `parameters.exposeRootDrivers:
false` to keep a nested-only export map (`client.qemu.power`,
`client.qemu.flasher`, `client.qemu.console`). The `tcp` driver is also
auto-injected (SSH host-forwarding on port 2222).

## How it works

When the ExporterSet scales up:

1. The controller creates an `Exporter` CR (cluster-side)
2. Once credentials are ready, it records the host assignment (and the
   credentials Secret name) on the Exporter CR - before touching the remote
   host, so cleanup can always find the instance
3. Connects to the remote host via SSH
4. Writes the exporter config YAML to `/etc/jumpstarter/exporters/`
5. Creates Podman quadlet `.container` files under `/etc/containers/systemd/`
   for both the **runtime** (QEMU) and **exporter** containers
6. Creates a shared Podman volume for inter-container communication
   (QEMU sockets)
7. Reloads systemd and starts the containers
8. Marks the Exporter CR deployed. Only then is the deployment considered
   complete; a partially failed deployment is retried on the next reconcile.

With `recycleStrategy: ExitAndReplace`, an instance whose containers start but
which never registers is recycled once the startup deadline (10 minutes) has
passed since its deployment completed, so an exporter that cannot reach the
controller cannot hold a pool slot indefinitely. A deployment that fails
outright - for example a quadlet that `systemctl start` rejects - never counts
as deployed: it is retried on the next reconcile and keeps its slot until it
succeeds.

When scaling down or cleaning up:

1. Stops the systemd services, bounded by `TimeoutStopSec` so a QEMU process
   that ignores `SIGTERM` cannot wedge the finalizer
2. Removes the quadlet files and exporter config
3. Reloads systemd
4. Force-removes any leftover containers and the shared Podman volume
5. Deletes the Exporter CR

Cleanup resolves the SSH user and port by merging the VirtualTargetClass and
ExporterSet parameters, so the class must still exist when the ExporterSet
goes away: delete the ExporterSet before its VirtualTargetClass.

It also reads the private key from the credentials Secret recorded on the
Exporter when it was deployed, so keep that Secret as well. If the Secret is
missing or SSH to the host fails, cleanup skips the remote teardown and lets
the finalizer clear - but the remote containers and volume are orphaned on the
host. Delete the class and the Secret only after the ExporterSet is gone.
If remote resources were orphaned (missing Secret, unreachable host), remove
them by hand on the host. The full teardown order matters - the runtime quadlet
sets `Restart=always` and the exporter quadlet `Restart=on-failure`, so removing
a container without stopping its service first will just restart it:

```bash
# 1. Stop the services (suppresses Restart=always)
sudo systemctl stop <name>-exporter <name>-runtime

# 2. Remove quadlet files and exporter config
sudo rm -f /etc/containers/systemd/<name>-exporter.container
sudo rm -f /etc/containers/systemd/<name>-runtime.container
sudo rm -f /etc/jumpstarter/exporters/<name>.yaml

# 3. Reload systemd so the generated units are gone
sudo systemctl daemon-reload

# 4. Force-remove any straggler containers
sudo podman rm -f <name>-exporter <name>-runtime

# 5. Remove the shared volume
sudo podman volume rm -f jumpstarter-<name>-shared
```

Replace `<name>` with the exporter name (shown in the Exporter CR's metadata).

### Container layout on the remote host

Each exporter instance creates two containers:

- **`<name>-runtime`**: Runs the QEMU emulator with access to `/dev/kvm`
  (if enabled) and the shared volume for communication sockets
- **`<name>-exporter`**: Runs the Jumpstarter exporter, connecting back to
  the cluster controller and mounting the shared volume

Both containers communicate via Unix sockets on the shared Podman volume
(`/shared/launcher.sock`).

## Leasing targets

Users lease off-cluster targets the same way as any other target - the
placement is transparent:

```bash
jmp shell -l board=aarch64-qemu,virtual=true
```

## Troubleshooting

### Check ExporterSet controller logs

```bash
kubectl logs -l component=exporterset-controller,provisioner=qemu-ssh-jumpstarter-dev \
  -n jumpstarter -f
```

### Check remote host containers

SSH into the lab host and inspect the Podman containers:

```bash
podman ps -a --filter "name=-(runtime|exporter)$"
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
- **Capacity full**: Increase `maxReplicas` in the ExporterSet or create
  additional ExporterSets for more hosts
