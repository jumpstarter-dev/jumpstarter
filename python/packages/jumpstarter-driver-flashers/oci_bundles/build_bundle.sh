#!/bin/sh

# oras login quay.io -u mangelajo

FLASHER_OCI_CONTAINER="${1:-quay.io/jumpstarter-dev/jumpstarter-flasher-test:latest}"
BUNDLE_FILES=${2:-"./test/"}

echo "Building and pushing ${FLASHER_OCI_CONTAINER}"

set -x

cd "${BUNDLE_FILES}"
DATA_FILES=
for file in $(find ./data -type f); do
	DATA_FILES="${DATA_FILES} ${file}:application/octet-stream "
done

oras push $FLASHER_OCI_CONTAINER \
	--artifact-type application/vnd.oci.bundle.v1 \
        ./manifest.yaml:application/yaml \
	$DATA_FILES
