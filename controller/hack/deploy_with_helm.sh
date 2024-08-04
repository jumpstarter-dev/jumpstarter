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
    GRPC_ROUTER_ENDPOINT="router.${BASEDOMAIN}:5080"
else
    echo -e "${GREEN}Deploying with nodeport ...${NC}"
    HELM_SETS="${HELM_SETS} --set jumpstarter-controller.grpc.nodeport.enabled=true"
    BASEDOMAIN="jumpstarter.${IP}.nip.io"
    GRPC_ENDPOINT="grpc.${BASEDOMAIN}:8082"
    GRPC_ROUTER_ENDPOINT="router.${BASEDOMAIN}:8083"
fi

HELM_SETS="${HELM_SETS} --set global.baseDomain=${BASEDOMAIN}"
HELM_SETS="${HELM_SETS} --set jumpstarter-controller.grpc.endpoint=${GRPC_ENDPOINT}"
HELM_SETS="${HELM_SETS} --set jumpstarter-controller.grpc.routerEndpoint=${GRPC_ROUTER_ENDPOINT}"

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
               --set global.timestamp=$(date +%s) \
               --values ./deploy/helm/jumpstarter/values.kind.yaml jumpstarter \
            ./deploy/helm/jumpstarter/

kubectl config set-context --current --namespace=jumpstarter-lab

echo -e "${GREEN}Waiting for grpc endpoints to be ready:${NC}"
for ep in ${GRPC_ENDPOINT} ${GRPC_ROUTER_ENDPOINT}; do
    RETRIES=30
    echo -e "${GREEN} * Checking ${ep} ... ${NC}"
    while ! podman run docker.io/fullstorydev/grpcurl -plaintext ${ep} list; do
        sleep 2
        RETRIES=$((RETRIES-1))
        if [ ${RETRIES} -eq 0 ]; then
            echo -e "${GREEN} * ${ep} not ready after 60s, exiting ... ${NC}"
            exit 1
        fi
    done
done



echo -e "${GREEN}Jumpstarter controller deployed successfully!${NC}"
echo -e " gRPC        endpoint: ${GRPC_ENDPOINT}"
echo -e " gRPC router endpoint: ${GRPC_ROUTER_ENDPOINT}"
