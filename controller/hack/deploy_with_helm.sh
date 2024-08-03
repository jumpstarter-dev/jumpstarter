#!/usr/bin/env bash
set -eo pipefail
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

KIND=${KIND:-bin/kind-v0.23.0}
IMG=${IMG:-quay.io/jumpstarter-dev/jumpstarter-controller:latest}
INGRESS_ENABLED=${INGRESS_ENABLED:-false}

GREEN='\033[0;32m'
NC='\033[0m' # No Color

METHOD=install
IP=$("${SCRIPT_DIR}"/get_ext_ip.sh)


kubectl config use-context kind-jumpstarter

HELM_SETS=""

if [ "${INGRESS_ENABLED}" == "true" ]; then
    echo -e "${GREEN}Deploying nginx ingress in kind ...${NC}"

    lsmod | grep ip_tables || \
      (echo "ip_tables module not loaded needed by nginx ingress, please run 'sudo modprobe ip_tables'" && exit 1)

    # before our helm installs, we make sure that kind has an ingress installed
    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

    echo -e "${GREEN}Waiting for nginx to be ready ...${NC}"

    while ! kubectl get pods --namespace ingress-nginx --selector=app.kubernetes.io/component=controller > /dev/null; do
      sleep 1
    done

    kubectl wait --namespace ingress-nginx \
      --for=condition=ready pod \
      --selector=app.kubernetes.io/component=controller \
      --timeout=90s

    HELM_SETS="${HELM_SETS} --set jumpstarter-controller.grpc.ingress.enabled=true"
    BASEDOMAIN="jumpstarter.${IP}.nip.io"
    GRPC_ENDPOINT="grpc.${BASEDOMAIN}:5080"
else
    echo -e "${GREEN}Deploying with nodeport ...${NC}"
    HELM_SETS="${HELM_SETS} --set jumpstarter-controller.grpc.nodeport.enabled=true"
    BASEDOMAIN="jumpstarter.127.0.0.1.nip.io"
    GRPC_ENDPOINT="grpc.${BASEDOMAIN}:5088"
fi

HELM_SETS="${HELM_SETS} --set global.baseDomain=${BASEDOMAIN}"

echo -e "${GREEN}Loading the ${IMG} in kind ...${NC}"
# load the docker image into the kind cluster
${KIND} load docker-image ${IMG} --name jumpstarter


# if we have an existing deployment, try to upgrade it instead
if helm list -A | grep jumpstarter > /dev/null; then
  METHOD=upgrade
fi

echo -e "${GREEN}Performing helm ${METHOD} ...${NC}"

# install/update with helm
helm ${METHOD} --namespace jumpstarter-lab \
               --create-namespace \
               ${HELM_SETS} \
               --values ./deploy/helm/jumpstarter/values.kind.yaml jumpstarter \
            ./deploy/helm/jumpstarter/

kubectl config set-context --current --namespace=jumpstarter-lab


echo -e "${GREEN}Jumpstarter controller deployed successfully!${NC}"
echo -e " GRPC endpoint: ${GRPC_ENDPOINT}"
