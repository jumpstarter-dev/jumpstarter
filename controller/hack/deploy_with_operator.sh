#!/usr/bin/env bash
set -exo pipefail
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

# Source common utilities
source "${SCRIPT_DIR}/utils"

# Source common deployment variables
source "${SCRIPT_DIR}/deploy_vars"

kubectl config use-context kind-jumpstarter

# Install nginx ingress if in ingress mode
if [ "${NETWORKING_MODE}" = "ingress" ]; then
    install_nginx_ingress
else
    echo -e "${GREEN}Deploying with nodeport ...${NC}"
fi

# load the container images into the kind cluster
kind_load_image "${IMG}"
kind_load_image "${OPERATOR_IMG}"

# Deploy the operator
echo -e "${GREEN}Deploying Jumpstarter operator ...${NC}"
kubectl apply -f deploy/operator/dist/install.yaml

# If operator deployment already exists, restart it to pick up the new image
if kubectl get deployment jumpstarter-operator-controller-manager -n jumpstarter-operator-system > /dev/null 2>&1; then
  echo -e "${GREEN}Restarting operator deployment to pick up new image ...${NC}"
  kubectl scale deployment jumpstarter-operator-controller-manager -n jumpstarter-operator-system --replicas=0
  kubectl wait --namespace jumpstarter-operator-system \
    --for=delete pod \
    --selector=control-plane=controller-manager \
    --timeout=60s 2>/dev/null || true
  kubectl scale deployment jumpstarter-operator-controller-manager -n jumpstarter-operator-system --replicas=1
fi

# Wait for operator to be ready
echo -e "${GREEN}Waiting for operator to be ready ...${NC}"
kubectl wait --namespace jumpstarter-operator-system \
  --for=condition=available deployment/jumpstarter-operator-controller-manager \
  --timeout=120s

# Create namespace for Jumpstarter deployment
echo -e "${GREEN}Creating jumpstarter-lab namespace ...${NC}"
kubectl create namespace jumpstarter-lab --dry-run=client -o yaml | kubectl apply -f -

# Generate Jumpstarter CR based on networking mode
echo -e "${GREEN}Creating Jumpstarter custom resource ...${NC}"

# Generate endpoint configuration based on networking mode
if [ "${NETWORKING_MODE}" == "ingress" ]; then
  CONTROLLER_ENDPOINT_CONFIG=$(cat <<-END
        - address: grpc.${BASEDOMAIN}:443
          ingress:
            enabled: true
            class: ""
END
)
  ROUTER_ENDPOINT_CONFIG=$(cat <<-END
        - address: router.${BASEDOMAIN}:443
          ingress:
            enabled: true
            class: ""
END
)
else
  CONTROLLER_ENDPOINT_CONFIG=$(cat <<-END
        # this is exposed by a nodeport in 30010 but mapped to 8082 on the host
        - address: grpc.${BASEDOMAIN}:8082
          nodeport:
            enabled: true
            port: 30010
END
)
  ROUTER_ENDPOINT_CONFIG=$(cat <<-END
        # this is exposed by a nodeport in 30011 but mapped to 8083 on the host
        - address: router.${BASEDOMAIN}:8083
          nodeport:
            enabled: true
            port: 30011
END
)
fi

# Apply the Jumpstarter CR with the appropriate endpoint configuration
cat <<EOF | kubectl apply -f -
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: jumpstarter-lab
spec:
  baseDomain: ${BASEDOMAIN}
  useCertManager: false
  controller:
    image: ${IMAGE_REPO}
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
${CONTROLLER_ENDPOINT_CONFIG}
    authentication:
      internal:
        prefix: "internal:"
        enabled: true
  routers:
    image: ${IMAGE_REPO}
    imagePullPolicy: IfNotPresent
    replicas: 1
    resources:
      requests:
        cpu: 100m
        memory: 100Mi
    grpc:
      endpoints:
${ROUTER_ENDPOINT_CONFIG}
EOF

# Set context to jumpstarter-lab namespace
kubectl config set-context --current --namespace=jumpstarter-lab

# Wait for Jumpstarter resources to be ready
wait_for_jumpstarter_resources

# Check gRPC endpoints are ready
check_grpc_endpoints

# Print success banner
print_deployment_success "operator"

