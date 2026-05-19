# Bootc Image

Lightweight edge deployment using MicroShift and a bootable container (bootc)
image with the Jumpstarter {term}`operator` pre-installed. Ideal for edge
devices, development environments, and small labs. Maintained by the community.

```{note}
This is a **community-supported** deployment. For production, use the
[Operator](operator.md) installation on Kubernetes or OpenShift.
```

## Prerequisites

- Fedora/RHEL-based system (tested on Fedora 42)
- Podman installed and configured
- Root/sudo access required for privileged operations
- At least 4GB RAM and 20GB disk space recommended

## Install

### Build the Image

```bash
make bootc-build
```

### Run as Container

```bash
make bootc-run
```

This creates a 1GB LVM disk image, starts MicroShift in a privileged container,
sets up LVM volume groups for TopoLVM, and waits for MicroShift to be ready.

### Create a Bootable QCOW2 Image

For bare-metal or VM deployments:

```bash
make build-image
```

```{note}
If the container is running, stop it first with `make bootc-rm` to avoid LVM
conflicts.
```

## Verify

Access the services:

- **Configuration Web UI**: `http://localhost:8880` (login: `root` / `jumpstarter`,
  password change required on first use)
- **MicroShift API**: `https://jumpstarter.<your-ip>.nip.io:6443`
- **Pod Monitoring**: `http://localhost:8880/pods`

Check running pods:

```bash
sudo podman exec -it jumpstarter-microshift-okd oc get pods -A
```

## Configuration

### Customization

```bash
BOOTC_IMG=quay.io/your-org/microshift-bootc:v1.0 make bootc-build
```

Add Kubernetes manifests to `/etc/microshift/manifests.d/002-jumpstarter/` by
editing `kustomization.yaml`. For live config service changes without rebuild:

```bash
make bootc-reload-app
```

### QCOW2 Image

The QCOW2 image is configured via `config.toml` (LVM partitioning with 20GB
minimum, XFS root filesystem, default password `root:jumpstarter`).

```bash
qemu-system-x86_64 \
    -m 4096 \
    -smp 2 \
    -drive file=output/qcow2/disk.qcow2,format=qcow2 \
    -net nic -net user,hostfwd=tcp::8880-:8880,hostfwd=tcp::443-:443
```

### Network

The system uses `nip.io` for automatic DNS resolution (e.g.
`jumpstarter.10.0.2.2.nip.io`).

| Port | Service | Description |
|------|---------|-------------|
| 80 | HTTP | MicroShift ingress |
| 443 | HTTPS | MicroShift API and ingress |
| 8880 | Config UI | Web configuration interface |
| 6443 | API Server | Kubernetes API (internal) |

### Security

1. **Default Password:** `root:jumpstarter`. Console login forces a change. Web
   UI requires a change before access.
2. **TLS Certificates:** MicroShift uses self-signed certs by default.
3. **Privileged Container:** Required for systemd, LVM, and networking.
4. **Authentication:** Web UI uses PAM authentication with root credentials.

## Troubleshooting

### LVM/TopoLVM Issues

```bash
sudo podman exec jumpstarter-microshift-okd vgs
sudo podman exec jumpstarter-microshift-okd pvs
make bootc-rm && make clean && make bootc-run
```

### MicroShift Not Starting

```bash
sudo podman logs jumpstarter-microshift-okd
sudo podman exec jumpstarter-microshift-okd journalctl -u microshift -f
```

### Configuration Service Issues

```bash
sudo podman exec jumpstarter-microshift-okd systemctl status config-svc
sudo podman exec jumpstarter-microshift-okd journalctl -u config-svc -f
```

## Uninstall

```bash
make bootc-stop
make bootc-rm
make clean
```

`make bootc-rm` stops the container, cleans up LVM volume groups, and detaches
loop devices. `make clean` removes the LVM disk image.

## Makefile Targets

| Target | Description |
|--------|-------------|
| `make bootc-build` | Build the bootc container image |
| `make bootc-run` | Run MicroShift in a container |
| `make bootc-stop` | Stop the running container |
| `make bootc-rm` | Remove container and clean up LVM resources |
| `make bootc-sh` | Open shell in container |
| `make bootc-reload-app` | Reload config service without rebuild |
| `make build-image` | Create bootable QCOW2 image |
| `make bootc-push` | Push image to registry |
| `make clean` | Clean up images, artifacts, and LVM disk |
