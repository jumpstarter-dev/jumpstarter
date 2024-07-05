#!/usr/bin/env bash
set -x -eo pipefail
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

KIND=${KIND:-bin/kind-v0.23.0}
IMG=${IMG:-quay.io/jumpstarter-dev/jumpstarter-controller:latest}


METHOD=install
IP=$("${SCRIPT_DIR}"/get_ext_ip.sh)



# if we have an existing deployment, try to upgrade it instead
if helm list --kube-context kind-jumpstarter -A | grep jumpstarter > /dev/null; then
  METHOD=upgrade
fi

# helm expects the namespaces to exist, and creating namespaces
# inside the helm charts is not recommended.
kubectl create namespace jumpstarter-lab --context kind-jumpstarter 2>/dev/null || true


${KIND} load docker-image ${IMG} --name jumpstarter


helm ${METHOD} --values ./deploy/helm/jumpstarter/values.kind.yaml jumpstarter \
            ./deploy/helm/jumpstarter/ --kube-context kind-jumpstarter

