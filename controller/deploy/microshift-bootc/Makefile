.PHONY: help build bootc-build push bootc-push bootc-run bootc-stop bootc-sh bootc-rm build-image build-iso

# Default image tags
BOOTC_IMG ?= quay.io/jumpstarter-dev/microshift/bootc:latest


help: ## Display this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Build

build: bootc-build ## Build bootc image (default target)

bootc-build: ## Build the bootc image with MicroShift
	@echo "Building bootc image: $(BOOTC_IMG): building as root to be on the container storage from root"
	sudo podman build -t $(BOOTC_IMG) -f Containerfile ../..

output/qcow2/disk.qcow2: ## Build a bootable QCOW2 image from the bootc image
	@echo "Building QCOW2 image from: $(BOOTC_IMG)"
	@echo "Running bootc-image-builder..."
	@mkdir -p output
	sudo podman run \
		--rm \
		-it \
		--privileged \
		--pull=newer \
		--security-opt label=type:unconfined_t \
		-v ./config.toml:/config.toml:ro \
		-v ./output:/output \
		-v /var/lib/containers/storage:/var/lib/containers/storage \
		quay.io/centos-bootc/bootc-image-builder:latest \
		--type qcow2 \
		-v \
		$(BOOTC_IMG)
	@echo "QCOW2 image built successfully in ./output/"

output/iso/disk.iso: ## Build a bootable ISO image from the bootc image
	@echo "Building ISO image from: $(BOOTC_IMG)"
	@echo "Running bootc-image-builder..."
	@mkdir -p output
	sudo podman run \
		--rm \
		-it \
		--privileged \
		--pull=newer \
		--security-opt label=type:unconfined_t \
		-v ./config.toml:/config.toml:ro \
		-v ./output:/output \
		-v /var/lib/containers/storage:/var/lib/containers/storage \
		quay.io/centos-bootc/bootc-image-builder:latest \
		--type iso \
		-v \
		$(BOOTC_IMG)
	@echo "ISO image built successfully in ./output/"

build-image: bootc-build ## Build the bootc based qcow2 image
	@echo "Building image: output/qcow2/disk.qcow2"
	@echo "Cleaning up any existing LVM resources to avoid conflicts..."
	-sudo vgs --noheadings -o vg_name,vg_uuid | grep myvg1 | while read vg uuid; do sudo vgremove -f --select vg_uuid=$$uuid 2>/dev/null || true; done
	-sudo losetup -D 2>/dev/null || true
	sudo rm -f output/qcow2/disk.qcow2
	make output/qcow2/disk.qcow2
	@echo "Image built successfully in ./output/"

build-iso: bootc-build ## Build the bootc based ISO image
	@echo "Building ISO image: output/iso/disk.iso"
	@echo "Cleaning up any existing LVM resources to avoid conflicts..."
	-sudo vgs --noheadings -o vg_name,vg_uuid | grep myvg1 | while read vg uuid; do sudo vgremove -f --select vg_uuid=$$uuid 2>/dev/null || true; done
	-sudo losetup -D 2>/dev/null || true
	sudo rm -f output/iso/disk.iso
	make output/iso/disk.iso
	@echo "ISO image built successfully in ./output/"

##@ Push

push: bootc-push ## Push bootc image to registry

bootc-push: ## Push the bootc image to registry
	@echo "Pushing bootc image: $(BOOTC_IMG)"
	sudo podman push $(BOOTC_IMG)

##@ Development

build-all: bootc-build ## Build bootc image

push-all: bootc-push ## Push bootc image to registry

bootc-run: ## Run MicroShift in a bootc container
	@echo "Running MicroShift container with image: $(BOOTC_IMG)"
	@BOOTC_IMG=$(BOOTC_IMG) sudo -E ./run-microshift.sh

bootc-stop: ## Stop the running MicroShift container
	@echo "Stopping MicroShift container..."
	-sudo podman stop jumpstarter-microshift-okd

bootc-rm: bootc-stop ## Remove the MicroShift container
	@echo "Removing MicroShift container..."
	-sudo podman rm -f jumpstarter-microshift-okd
	@echo "Cleaning up LVM resources..."
	-sudo vgremove -f myvg1 2>/dev/null || true
	-sudo losetup -d $$(sudo losetup -j /var/lib/microshift-okd/lvmdisk.image | cut -d: -f1) 2>/dev/null || true
	@echo "LVM cleanup complete"

bootc-sh: ## Open a shell in the running MicroShift container
	@echo "Opening shell in MicroShift container..."
	sudo podman exec -it jumpstarter-microshift-okd /bin/bash -l

bootc-reload-app: ## Reload the config service app without rebuilding (dev mode)
	@echo "Reloading config-svc app..."
	sudo podman cp config-svc/app.py jumpstarter-microshift-okd:/usr/local/bin/config-svc
	sudo podman exec jumpstarter-microshift-okd systemctl restart config-svc
	@echo "Config service reloaded successfully!"

clean: ## Clean up local images and build artifacts
	@echo "Removing local images..."
	-sudo podman rmi $(BOOTC_IMG)
	@echo "Removing QCOW2 output..."
	-sudo rm -rf output/qcow2/disk.qcow2
	@echo "Removing ISO output..."
	-sudo rm -rf output/iso/disk.iso
	@echo "Removing LVM disk image..."
	-sudo rm -f /var/lib/microshift-okd/lvmdisk.image

