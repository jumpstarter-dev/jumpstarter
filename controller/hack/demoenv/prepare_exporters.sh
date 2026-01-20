#!/bin/sh
OUT_DIR=${OUT_DIR:-"hack/demoenv/gen"}
NAMESPACE=${NAMESPACE:-"jumpstarter-lab"}

mkdir -p ${OUT_DIR}
for i in `seq 0 4`; do
    EXPORTER_NAME="exporter-$i"
    echo "Creating exporter $EXPORTER_NAME"
    OUT_FILE="${OUT_DIR}/${EXPORTER_NAME}.yaml"
    bin/jmpctl exporter delete "${EXPORTER_NAME}" --namespace "${NAMESPACE}" > /dev/null 2>&1
    bin/jmpctl exporter create "${EXPORTER_NAME}" --namespace "${NAMESPACE}" > "${OUT_FILE}"
    cat >> "${OUT_FILE}" <<EOF
export:
    storage:
        type: jumpstarter.drivers.storage.driver.MockStorageMux
    power:
        type: jumpstarter.drivers.power.driver.MockPower
    echonet:
        type: jumpstarter.drivers.network.driver.EchoNetwork
    tcpnet:
        type: jumpstarter.drivers.network.driver.TcpNetwork
        config:
            host: "192.168.1.52"
            port: 80

EOF
    kubectl label exporter -n "${NAMESPACE}" "${EXPORTER_NAME}" device-type=mock
done

for i in `seq 0 4`; do
    EXPORTER_NAME="vcan-exporter-$i"
    echo "Creating exporter $EXPORTER_NAME"
    OUT_FILE="${OUT_DIR}/${EXPORTER_NAME}.yaml"
    bin/jmpctl exporter delete "${EXPORTER_NAME}" --namespace "${NAMESPACE}" > /dev/null 2>&1
    bin/jmpctl exporter create "${EXPORTER_NAME}" --namespace "${NAMESPACE}" > "${OUT_FILE}"
    cat >> "${OUT_FILE}" <<EOF
export:
    storage:
        type: jumpstarter.drivers.storage.driver.MockStorageMux
    power:
        type: jumpstarter.drivers.power.driver.MockPower
    echonet:
        type: jumpstarter.drivers.network.driver.EchoNetwork
    can:
        type: jumpstarter_driver_can.driver.Can
        config:
            channel: 1
            interface: "virtual"

EOF
    kubectl label exporter -n "${NAMESPACE}" "${EXPORTER_NAME}" device-type=can
done

kubectl delete statefulset -n jumpstarter-exporters exporter vcan-exporter
kubectl delete pod --all -n jumpstarter-exporters --force --grace-period=0

kubectl create namespace jumpstarter-exporters || true
kubectl apply -k ./hack/demoenv/

echo "Waiting for exporters to be ready...."

kubectl wait --for=condition=Ready statefulset -n jumpstarter-exporters exporter --timeout=60s || \
    kubectl describe pod -n jumpstarter-exporters exporter-0 && \
    kubectl logs -n jumpstarter-exporters exporter-0
kubectl wait --for=condition=Ready statefulset -n jumpstarter-exporters vcan-exporter --timeout=60s || \
    kubectl describe pod -n jumpstarter-exporters vcan-exporter-0 && \
    kubectl logs -n jumpstarter-exporters vcan-exporter-0
