# Manage Exporters

The Jumpstarter CLI can be used to manage your exporter configurations.

## Creating a exporter

To connect a device to Jumpstarter, an exporter instance must be registered.

Exporter creation must be done by an administrator user who has access to
the Kubernetes cluster where the `jumpstarter-controller` service is hosted.

```bash
# Specify the location of the kubeconfig to use
export KUBECONFIG=/path/to/kubeconfig
# Create the exporter instance
jumpstarter exporter create my-exporter -o my-exporter.yaml
```

This creates an exporter named `my-exporter` and outputs the configuration to a
YAML file called `my-exporter.yaml`:

```yaml
exporter:
    name: my-exporter
    endpoint: "grpcs://jumpstarter.my-lab.com:1443"
    token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
    # environmentConfig: /etc/jumpstarter/environment.py
```

Creating an exporter registers the custom resource object in the k8s API, the
`jumpstarter-controller` will create an authentication token and attach it to
the object.

## Running an Exporter

The exporter service can be run as a container either within the same cluster
(using node affinity) or on a remote machine that has access to the cluster over
the network.

### Running using Podman

To run the exporter container on a test runner using Podman:

```bash
# Must be run as privileged to access hardware
podman run --cap-add=all --privileged \
        -v /dev:/dev -v /lib/modules:/lib/modules -v /etc/jumpstarter/:/etc/jumpstarter \
        quay.io/jumpstarter-dev/exporter -c my-exporter.yaml

# additional flags like could be necessary depending on the drivers:
#  --security-opt label=disable
#  --security-opt seccomp=unconfined
```

#### Running as a Service

To run the exporter as a service on a test runner with Jumpstarter installed:

```bash
jumpstarter config set-exporter my-exporter
sudo systemctl start jumpstarter 
```

<!-- TODO: create instructions to setup as quadlets with podman and systemd 
https://www.redhat.com/sysadmin/quadlet-podman -->