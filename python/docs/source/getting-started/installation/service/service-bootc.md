# Bootc Image

Lightweight edge deployment using MicroShift and a bootable container (bootc)
image with the Jumpstarter {term}`operator` pre-installed. Maintained by the
community.

```{note}
This is a **community-supported** deployment intended for development, testing,
and small lab environments. For production deployments, use the
[Operator](service-production.md) installation on Kubernetes or OpenShift.
```

## Overview

This deployment provides a lightweight, all-in-one solution ideal for:
- Edge devices with limited resources
- Development and testing environments
- Proof-of-concept deployments
- Local experimentation with Jumpstarter

Features:
- MicroShift 4.20 (OKD) -- lightweight Kubernetes distribution
- Jumpstarter {term}`operator` -- pre-installed and ready to use
- TopoLVM CSI -- dynamic storage provisioning using LVM
- Configuration web UI -- easy setup and management at port 8880
- Pod monitoring -- real-time pod status dashboard

## Prerequisites

- Fedora/RHEL-based system (tested on Fedora 42)
- Podman installed and configured
- Root/sudo access required for privileged operations
- At least 4GB RAM and 20GB disk space recommended

## Quick Start

### 1. Build the Bootc Image

```bash
make bootc-build
```

This builds a container image with MicroShift and all dependencies.

### 2. Run as Container

```bash
make bootc-run
```

This will:
- Create a 1GB LVM disk image at `/var/lib/microshift-okd/lvmdisk.image`
- Start MicroShift in a privileged container
- Set up LVM volume groups inside the container for TopoLVM
- Wait for MicroShift to be ready

Output example:
```
MicroShift is running in a bootc container
Hostname:  jumpstarter.10.0.2.2.nip.io
Container: jumpstarter-microshift-okd
LVM disk:  /var/lib/microshift-okd/lvmdisk.image
VG name:   myvg1
Ports:     HTTP:80, HTTPS:443, Config Service:8880
```

### 3. Access the Services

#### Configuration Web UI
- URL: `http://localhost:8880`
- Login: `root` / `jumpstarter` (default -- you will be required to change it)
- Features:
  - Configure hostname and base domain
  - Set {term}`controller` image version
  - Change root password (required on first use)
  - Download kubeconfig
  - Monitor pod status

#### MicroShift API
- URL: `https://jumpstarter.<your-ip>.nip.io:6443`
- Download kubeconfig from the web UI or extract from container

#### Pod Monitoring Dashboard
- URL: `http://localhost:8880/pods`
- Auto-refreshes every 5 seconds
- Shows all pods across all namespaces

## Container Management

### View Running Pods

```bash
sudo podman exec -it jumpstarter-microshift-okd oc get pods -A
```

### Open Shell in Container

```bash
make bootc-sh
```

### Stop Container

```bash
make bootc-stop
```

### Remove Container

```bash
make bootc-rm
```

This will:
- Stop the container
- Remove the container
- Clean up LVM volume groups (myvg1)
- Detach loop devices

The LVM disk image (`/var/lib/microshift-okd/lvmdisk.image`) is preserved. To
remove it completely, use `make clean`.

### Complete Rebuild

```bash
make bootc-rm bootc-build bootc-run
```

## Creating a Bootable QCOW2 Image

For bare-metal or VM deployments, create a bootable QCOW2 disk image:

### Build QCOW2 Image

```bash
make build-image
```

This will:
1. Clean up any existing LVM resources to avoid conflicts
2. Build the bootc container image (if not already built)
3. Use `bootc-image-builder` to create a bootable QCOW2 image
4. Output the image to `./output/qcow2/disk.qcow2`

```{note}
If the container is running (`make bootc-run`), stop it first with
`make bootc-rm` to avoid LVM conflicts.
```

### Configuration

The QCOW2 image is configured via `config.toml`:
- LVM partitioning: creates `myvg1` volume group with 20GB minimum
- Root filesystem: XFS on LVM (10GB minimum)
- Default password: `root:jumpstarter` (change via web UI on first boot)

### Using the QCOW2 Image

#### In a Virtual Machine (KVM/QEMU)

```bash
qemu-system-x86_64 \
    -m 4096 \
    -smp 2 \
    -drive file=output/qcow2/disk.qcow2,format=qcow2 \
    -net nic -net user,hostfwd=tcp::8880-:8880,hostfwd=tcp::443-:443
```

#### Convert to Other Formats

```bash
qemu-img convert -f qcow2 -O raw output/qcow2/disk.qcow2 output/disk.raw
qemu-img convert -f qcow2 -O vdi output/qcow2/disk.qcow2 output/disk.vdi
```

## Architecture

```
Bootc Container / Image
  - Fedora CoreOS 9 base
  - MicroShift 4.20 (OKD)
  - Jumpstarter Operator
  - TopoLVM CSI (storage)
  - Configuration Service (Python/Flask)
  - Firewalld (ports 22, 80, 443, 8880)
```

### Storage Setup

When running as a container:
1. Script creates `/var/lib/microshift-okd/lvmdisk.image` (1GB)
2. Image is copied into the container
3. Loop device is created inside container
4. LVM volume group `myvg1` is created
5. TopoLVM uses `myvg1` for dynamic PV provisioning

When deployed from QCOW2:
1. Bootc image builder creates proper disk partitioning
2. LVM volume group `myvg1` is set up on disk
3. Root filesystem uses part of the VG
4. Remaining space available for TopoLVM

## Customization

### Change Default Image

```bash
BOOTC_IMG=quay.io/your-org/microshift-bootc:v1.0 make bootc-build
```

### Modify Manifests

Add Kubernetes manifests to `/etc/microshift/manifests.d/002-jumpstarter/` by
editing `kustomization.yaml`. Additional YAML files will be automatically
applied.

### Update Configuration Service

Edit `config-svc/app.py` and rebuild:

```bash
make bootc-build
```

For live testing without rebuild:

```bash
make bootc-reload-app
```

## Troubleshooting

### LVM/TopoLVM Issues

Check if volume group exists in container:

```bash
sudo podman exec jumpstarter-microshift-okd vgs
sudo podman exec jumpstarter-microshift-okd pvs
```

If TopoLVM pods are crashing, recreate the LVM setup:

```bash
make bootc-rm
make clean
make bootc-run
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

### Port Conflicts

If ports 80, 443, or 8880 are in use, modify `run-microshift.sh`:

```bash
HTTP_PORT=8080
HTTPS_PORT=8443
CONFIG_SVC_PORT=9880
```

## Makefile Targets

| Target | Description |
|--------|-------------|
| `make help` | Display all available targets |
| `make bootc-build` | Build the bootc container image |
| `make bootc-run` | Run MicroShift in a container |
| `make bootc-stop` | Stop the running container |
| `make bootc-rm` | Remove container and clean up LVM resources |
| `make bootc-sh` | Open shell in container |
| `make bootc-reload-app` | Reload config service without rebuild |
| `make build-image` | Create bootable QCOW2 image |
| `make bootc-push` | Push image to registry |
| `make clean` | Clean up images, artifacts, and LVM disk |

## Network Configuration

### Hostname Resolution

The system uses `nip.io` for automatic DNS resolution:
- Default: `jumpstarter.<host-ip>.nip.io`
- Example: `jumpstarter.10.0.2.2.nip.io` resolves to `10.0.2.2`

### Firewall Ports

| Port | Service | Description |
|------|---------|-------------|
| 80 | HTTP | MicroShift ingress |
| 443 | HTTPS | MicroShift API and ingress |
| 8880 | Config UI | Web configuration interface |
| 6443 | API Server | Kubernetes API (internal) |

## Security Notes

1. **Default Password:** The system ships with `root:jumpstarter` as the default
   password. Console login forces a password change. The web UI requires a
   password change before access.
2. **TLS Certificates:** MicroShift uses self-signed certs by default.
3. **Privileged Container:** Required for systemd, LVM, and networking.
4. **Authentication:** Web UI uses PAM authentication with root credentials.

## Development Workflow

```bash
vim config-svc/app.py
make bootc-reload-app
curl http://localhost:8880
make bootc-sh
journalctl -u config-svc -f
make bootc-rm bootc-build bootc-run
```

## Deploying to Bare Metal or VM

1. Build QCOW2 image:
   ```bash
   make build-image
   ```

2. Copy image to target system:
   ```bash
   scp output/qcow2/disk.qcow2 target-host:/var/lib/libvirt/images/
   ```

3. Create VM or write to disk:
   ```bash
   virt-install --name jumpstarter \
       --memory 4096 \
       --vcpus 2 \
       --disk path=/var/lib/libvirt/images/disk.qcow2 \
       --import \
       --os-variant fedora39
   ```

4. First boot:
   - Console login will require password change from default `jumpstarter`
   - Access web UI at `http://<host-ip>:8880` and set new password
