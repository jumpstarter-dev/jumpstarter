# Standalone

The community provides an experimental standalone/lightweight bootable container
image including MicroShift, the Jumpstarter {term}`operator`, and a small web UI.

This is ideal for small development environments, and small labs.
For production deployments where high availability is desired
see [Production](production.md).

## Prerequisites

- An Intel device, capable of running CentOS 10
- At least 4GB RAM and 40GB disk space recommended

## Install

### Method 1: Create a CentOS install ISO with a kickstart.ks configuration attached

From a Linux system, for example Fedora:

```console
export CENTOS_ISO=CentOS-Stream-10-latest-x86_64-boot.iso
curl -o "${CENTOS_ISO}" "https://mirrors.centos.org/mirrorlist?path=/10-stream/BaseOS/x86_64/iso/${CENTOS_ISO}&redirect=1&protocol=https"
curl -o kickstart.ks https://raw.githubusercontent.com/jumpstarter-dev/jumpstarter/refs/heads/main/controller/deploy/microshift-bootc/kickstart.ks
mkksiso kickstart.ks ${CENTOS_ISO} cs10-js-install.iso
```

Flash the .iso to a pendrive, and boot/install your system, watch out and remove the pendrive once install has finished.

### Method 2: Using system-reinstall-bootc

From a bootc capable system (Fedora or CentOS 10), run the following command,
please note that this action is **destructive** and will re-install your system.

```console
sudo dnf -y install system-reinstall-bootc
sudo system-reinstall-bootc quay.io/jumpstarter-dev/microshift/bootc:latest
sudo reboot
```

## Setup

Once installed and booted, the system displays a banner with the HTTP management interface URL, for example:
`http://jumpstarter.192.168.1.11.nip.io:8880/`

The default password for root will be `jumpstarter` and you will be requested to change it on the first connection.

### Network

The system uses `nip.io` for automatic DNS resolution (e.g.
`jumpstarter.10.0.2.2.nip.io`) although you can setup your own domain if you
have control over your DNS.

| Port | Service | Description |
|------|---------|-------------|
| 80 | HTTP | MicroShift ingress |
| 443 | HTTPS | MicroShift ingress |
| 8880 | Config UI | Web configuration interface |
| 6443 | API Server | Kubernetes API (internal) |

### Security

1. **Default Password:** `root:jumpstarter`. Console login forces a change. Web
   UI requires a change before access.
2. **TLS Certificates:** This deployment uses self-signed certs by default.
3. **Authentication:** Web UI uses PAM authentication with root credentials.

## Development

This section covers working with and modifying the bootc image for development purposes.

### Prerequisites

- Fedora/RHEL-based system (tested on Fedora 42)
- Podman installed and configured
- Root/sudo access required for privileged operations
- At least 4GB RAM and 20GB disk space recommended

### Build the Bootc Image

From the `controller/deploy/microshift-bootc/` directory:

```console
make bootc-build
```

To build for multiple architectures (amd64 and arm64):

```console
make bootc-build-multi
```

### Test in a Container

Run MicroShift in a privileged container for testing:

```console
make bootc-run
```

This creates a 1GB LVM disk image, starts MicroShift in a privileged container,
sets up LVM volume groups for TopoLVM, and waits for MicroShift to be ready.

Access the services:
- **Configuration Web UI**: `http://localhost:8880` (login: `root` / `jumpstarter`)
- **MicroShift API**: `https://jumpstarter.<your-ip>.nip.io:6443`
- **Pod Monitoring**: `http://localhost:8880/pods`

Check running pods:

```console
sudo podman exec -it jumpstarter-microshift-okd oc get pods -A
```

### Build Bootable Images

Create a QCOW2 image for VMs or bare-metal deployments:

```console
make build-image
```

Create an ISO installer image:

```console
make build-iso
```

```{note}
If the container is running, stop it first with `make bootc-rm` to avoid LVM conflicts.
```

The images are configured via `config.toml` with LVM partitioning (20GB minimum),
XFS root filesystem, and default password `root:jumpstarter`.

### Build Kickstart ISO

To create a kickstart ISO that automates the installation:

```console
make download-centos-iso  # Downloads CentOS Stream 10 ISO
make build-ks-iso          # Creates kickstart ISO
```

For ARM64 builds:

```console
make download-centos-iso ARCH=aarch64
make build-ks-iso ARCH=aarch64
```

### Development Workflow

For iterative development of the web UI without rebuilding the entire image:

```console
make bootc-reload-app
```

This copies the updated Python modules and templates into the running container
and restarts the config service.

### Shell Access

Open a shell in the running container:

```console
make bootc-sh
```

### Troubleshooting

#### LVM/TopoLVM Issues

```console
sudo podman exec jumpstarter-microshift-okd vgs
sudo podman exec jumpstarter-microshift-okd pvs
make bootc-rm && make clean && make bootc-run
```

#### MicroShift Not Starting

```console
sudo podman logs jumpstarter-microshift-okd
sudo podman exec jumpstarter-microshift-okd journalctl -u microshift -f
```

#### Configuration Service Issues

```console
sudo podman exec jumpstarter-microshift-okd systemctl status config-svc
sudo podman exec jumpstarter-microshift-okd journalctl -u config-svc -f
```

### Cleanup

Stop and remove the container:

```console
make bootc-rm
```

Remove all build artifacts and images:

```console
make clean
```

### Customization

Override the default image tag:

```console
BOOTC_IMG=quay.io/your-org/microshift-bootc:v1.0 make bootc-build
```

Add Kubernetes manifests by editing the kustomization.yaml file in
`controller/deploy/microshift-bootc/manifests/` - they will be deployed to
`/etc/microshift/manifests.d/002-jumpstarter/` in the image.

### Push to Registry

Push single-architecture image:

```console
make bootc-push
```

Push multi-architecture manifest:

```console
make bootc-push-multi
```
