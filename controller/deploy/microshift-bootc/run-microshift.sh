#!/bin/bash
set -euo pipefail

# Use the image from environment or default
IMAGE=${BOOTC_IMG:-"quay.io/jumpstarter-dev/microshift/bootc:latest"}
CONTAINER_NAME=${CONTAINER_NAME:-"jumpstarter-microshift-okd"}

LVM_DISK="/var/lib/microshift-okd/lvmdisk.image"
VG_NAME="myvg1"
HTTP_PORT=80
HTTPS_PORT=443
CONFIG_SVC_PORT=8880


function pull_bootc_image() {
    local -r image_ref="$1"

    # Skip pulling the local container images
    if [[ "${image_ref}" == localhost/* ]]; then
        echo "Skipping pull of local container image: ${image_ref}"
        return 0
    fi

    # Check if the image already exists locally
    if podman image exists "${image_ref}"; then
        echo "Image '${image_ref}' already exists locally, skipping pull"
        return 0
    fi

    echo "Pulling '${image_ref}'"
    podman pull "${image_ref}"
}

function prepare_lvm_disk() {
    local -r lvm_disk="$1"
    local -r vg_name="$2"

    mkdir -p "$(dirname "${lvm_disk}")"
    
    if [ ! -f "${lvm_disk}" ]; then
        echo "Creating LVM disk image: ${lvm_disk}"
        truncate --size=1G "${lvm_disk}"
    else
        echo "INFO: '${lvm_disk}' already exists, reusing it."
    fi
}

function setup_lvm_in_container() {
    local -r container_name="$1"
    local -r lvm_disk="$2"
    local -r vg_name="$3"
    
    echo "Setting up LVM inside container..."
    
    # Check if VG already exists in container
    if podman exec "${container_name}" vgs "${vg_name}" &>/dev/null; then
        echo "Volume group '${vg_name}' already exists in container"
        return 0
    fi
    
    # Copy the LVM disk into the container
    local container_lvm_disk="/var/lib/lvmdisk.image"
    podman cp "${lvm_disk}" "${container_name}:${container_lvm_disk}"
    
    # Set up loop device and create VG inside the container
    podman exec "${container_name}" bash -c "
        set -e
        # Find available loop device
        LOOP_DEV=\$(losetup --find --show --nooverlap '${container_lvm_disk}')
        echo \"Created loop device: \${LOOP_DEV}\"
        
        # Create volume group
        vgcreate -f -y '${vg_name}' \"\${LOOP_DEV}\"
        echo \"Created volume group: ${vg_name}\"
        
        # Verify
        vgs '${vg_name}'
    "
}

function run_bootc_image() {
    local -r image_ref="$1"
    local -r container_name="$2"

    # Get the default route IP address
    local -r hostname="jumpstarter.127-0-0-1.nip.io"

    # Prerequisites for running the MicroShift container:
    # - If the OVN-K CNI driver is used, the `openvswitch` module must be loaded on the host.
    # - If the TopoLVM CSI driver is used, the /dev/dm-* device must be shared with the container.
    echo "Running '${image_ref}' as container '${container_name}'"
    echo "Hostname: ${hostname}"
    modprobe openvswitch || true

    # Share the /dev directory with the container to enable TopoLVM CSI driver.
    # Mask the devices that may conflict with the host by sharing them on a
    # temporary file system. Note that a pseudo-TTY is also allocated to
    # prevent the container from using host consoles.
    local vol_opts="--tty --volume /dev:/dev"
    for device in input snd dri; do
        [ -d "/dev/${device}" ] && vol_opts="${vol_opts} --tmpfs /dev/${device}"
    done
    set -x
    # shellcheck disable=SC2086
    podman run --privileged -d \
        --replace \
        ${vol_opts} \
        -p ${CONFIG_SVC_PORT}:8880 \
        -p ${HTTP_PORT}:80 \
        -p ${HTTPS_PORT}:443 \
        --name "${container_name}" \
        --hostname "${hostname}" \
        "${image_ref}"
    set +x

    echo "Waiting for MicroShift to start"
    local -r kubeconfig="/var/lib/microshift/resources/kubeadmin/kubeconfig"
    local -r max_wait=${MICROSHIFT_KUBECONFIG_TIMEOUT:-300}
    local start_time
    start_time=$(date +%s)
    
    while true ; do
        if podman exec "${container_name}" /bin/test -f "${kubeconfig}" &>/dev/null ; then
            break
        fi
        
        local current_time
        current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [ "${elapsed}" -ge "${max_wait}" ]; then
            echo "ERROR: Timeout waiting for MicroShift kubeconfig after ${elapsed} seconds" >&2
            echo "ERROR: Container: ${container_name}, Kubeconfig path: ${kubeconfig}" >&2
            return 1
        fi
        
        sleep 1
    done
}

# Check if the script is running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

# Run the procedures
pull_bootc_image     "${IMAGE}"
prepare_lvm_disk     "${LVM_DISK}" "${VG_NAME}"
run_bootc_image      "${IMAGE}" "${CONTAINER_NAME}"
setup_lvm_in_container "${CONTAINER_NAME}" "${LVM_DISK}" "${VG_NAME}"

# Get the hostname for display
HOSTNAME="jumpstarter.127-0-0-1.nip.io"

# Follow-up instructions
echo
echo "MicroShift is running in a bootc container"
echo "Hostname:  ${HOSTNAME}"
echo "Container: ${CONTAINER_NAME}"
echo "LVM disk:  ${LVM_DISK}"
echo "VG name:   ${VG_NAME}"
echo "Ports:     HTTP:${HTTP_PORT}, HTTPS:${HTTPS_PORT}, Config Service:${CONFIG_SVC_PORT}"
echo
echo "To access the container, run the following command:"
echo " - make bootc-sh"
echo
echo "To verify that MicroShift pods are up and running, run the following command:"
echo " - sudo podman exec -it ${CONTAINER_NAME} oc get pods -A"
echo
echo "To access the web interfaces, visit:"
echo " - Config Service: http://${HOSTNAME%%.*}:${CONFIG_SVC_PORT} or http://localhost:${CONFIG_SVC_PORT}"
echo " - MicroShift:     https://${HOSTNAME}"
echo
echo "To stop MicroShift, run the following command:"
echo " - make bootc-stop"
echo "To remove the container, run the following command:"
echo " - make bootc-rm"

