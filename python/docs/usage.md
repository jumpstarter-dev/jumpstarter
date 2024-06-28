

# Usage

## Administrator tasks

### Creating a client token and configuration
```bash
jumpstarter create client my-client -o my-client.yaml
```

my-client.yaml:
```yaml
client:
    name: my-client
    endpoint: "grpcs://jumpstarter.my-lab.com:1443"
    token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNDEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIzNDEyMzQxMjM0LXF3ZXJxd2VycXdlcnF3ZXJxd2VycXdlcnF3ZXIK
```

### Creating a exporter

Creating an exporter registers the CR object in the k8s API, the jumpstarter-controller will create
an authentication token and attach it to the object.

From the admin point of view it's performed running the following command:

```bash
export KUBECONFIG=/path/to/kubeconfig
jumpstarter create exporter my-exporter -o my-exporter.yaml
```

This results in a base exporter configuration file

my-exporter.yaml:
```yaml
exporter:
    name: my-client
    endpoint: "grpcs://jumpstarter.my-lab.com:1443"
    token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNDEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIzNDEyMzQxMjM0LXF3ZXJxd2VycXdlcnF3ZXJxd2VycXdlcnF3ZXIK
    # environmentConfig: /etc/jumpstarter/environment.py
```


### Running a exporter

```bash
podman run --cap-add=all --privileged \
       -v /dev:/dev -v /lib/modules:/lib/modules -v /etc/jumpstarter/:/etc/jumpstarter \
       quay.io/jumpstarter-dev/exporter -c /etc/jumpstarter/my-exporter.yaml

# additional flags like could be necessary depending on the drivers:
#  --security-opt label=disable
#  --security-opt seccomp=unconfined
```

<!-- TODO: create instructions to setup as quadlets with podman and systemd https://www.redhat.com/sysadmin/quadlet-podman -->

## User/Client tasks

### Authentication/configuration

By default the libraries and clients will look for the `~/.jumpstarter/client.yaml` file, this file
is a client file that contains the endpoint and token to authenticate with the Jumpstarter service.

Alternatively the client can receive the endpoint and token as environment variables:

```bash
export JUMPSTARTER_ENDPOINT=grpcs://jumpstarter.my-lab.com:1443
export JUMPSTARTER_TOKEN=dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNDEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIzNDEyMzQxMjM0LXF3ZXJxd2VycXdlcnF3ZXJxd2VycXdlcnF3ZXIK
```

This is useful for CI/CD systems that inject the environment variables into the pipeline.

### Running tests locally (without a server)

When no client configuration or environment variables are set, the client will
run in local mode and will use the local resources. Known hardware could be
auto-detected and used by the client, but specific hardware can be configured
with an environment python file.

### Running tests through a central server

When client configuration exists


