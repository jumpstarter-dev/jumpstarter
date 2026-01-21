#!/bin/sh

# oras login quay.io -u mangelajo

set -e

FLASHER_OCI_CONTAINER="${1:-quay.io/jumpstarter-dev/jumpstarter-flasher-test:latest}"
BUNDLE_FILES=${2:-"./test/"}

echo "Building and pushing ${FLASHER_OCI_CONTAINER}"

set -x

cd "${BUNDLE_FILES}"
MANIFESTS=
for file in $(ls -1 *.yaml); do
	MANIFESTS="${MANIFESTS} ${file}:application/yaml "
done
DATA_FILES=
for file in $(find ./data -type f -prune -a -not -name .gitkeep); do
	DATA_FILES="${DATA_FILES} ${file}:application/octet-stream "
done

oras push $FLASHER_OCI_CONTAINER \
	--artifact-type application/vnd.oci.bundle.v1 \
	$MANIFESTS \
	$DATA_FILES
