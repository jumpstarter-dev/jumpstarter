# Raspberry Pi

This tutorial is meant to cover the end-to-end process of deploying Jumpstarter to
test an embedded IoT device. This includes deploying the Jumpstarter Service on a local
Kubernetes cluster running on your development system, deploying a Raspberry Pi exporter,
and testing a small embedded application running on a Raspberry Pi Pico W with Jumpstarter.

Note that not all of the hardware is explicitly required to complete this tutorial, however,
it is recommended to at least have a Raspberry Pi Pico W so you can use it as the Device Under Test (DUT).

## Required Hardware

- A Linux or macOS system (WSL should work on Windows)
- 1x Raspberry Pi 3 or 4
- 1x SD Card (Class 10 or higher)
- 1x Raspberry Pi 3/4 Power Supply
- 1x Raspberry Pi Pico W
- 1x LED (any color)
- 1x 200-500 Ohm Resistor
- 2x Male-to-Female Jumper Cables
- 1x Breadboard (optional)

## Exercises

### 1. Installing the Jumpstarter CLI

First, we must install the Jumpstarter CLI on our Linux or macOS device.
The easiest way to do this is to Python virtual environment.

````{tab} Global
```{code-block} console
:substitutions:
# Install with pip
$ pip3 install --extra-index-url {{index_url}} jumpstarter-all

# Create config directories for Jumpstarter
$ mkdir -p "${HOME}/.config/jumpstarter/"
$ sudo mkdir /etc/jumpstarter
$ sudo chown -R $USER:$USER /etc/jumpstarter
```
````

````{tab} Pip venv
```{code-block} console
:substitutions:
# Create a new virtual environment
$ python3 -m venv ~/.venv/jumpstarter
$ source ~/.venv/jumpstarter/bin/activate

# Install with pip
$ pip3 install --extra-index-url {{index_url}} jumpstarter-all

# Create config directories for Jumpstarter
$ mkdir -p "${HOME}/.config/jumpstarter/"
$ sudo mkdir /etc/jumpstarter
$ sudo chown -R $USER:$USER /etc/jumpstarter
```
````

### 2. Installing the Jumpstarter Service

For this tutorial we will use Kind to create a local Kubernetes cluster that can be used to install the Jumpstarter Service locally.

Kind is available through both Docker Desktop and Podman Desktop. You can also install Kind independently as a CLI tool on macOS and Linux.

First, create a kind cluster config that enables nodeports to host the Services.
Save this as `kind_config.yaml`:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
kubeadmConfigPatches:
- |
  kind: ClusterConfiguration
  apiServer:
    extraArgs:
      "service-node-port-range": "3000-32767"
- |
  kind: InitConfiguration
  nodeRegistration:
    kubeletExtraArgs:
      node-labels: "ingress-ready=true"
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 80
    hostPort: 5080
    protocol: TCP
  - containerPort: 30010
    hostPort: 8082
    protocol: TCP
  - containerPort: 30011
    hostPort: 8083
    protocol: TCP
  - containerPort: 443
    hostPort: 5443
    protocol: TCP
```

Next, create a kind cluster using the config you created:

```console
$ kind create cluster --config kind_config.yaml
```

Once your cluster has been created, install the Jumpstarter service using helm:

```{code-block} console
:substitutions:
$ export IP="X.X.X.X" # Insert your computer's IP address here
$ export BASEDOMAIN="jumpstarter.${IP}.nip.io"
$ export GRPC_ENDPOINT="grpc.${BASEDOMAIN}:8082"
$ export GRPC_ROUTER_ENDPOINT="router.${BASEDOMAIN}:8083"
$ helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
            --create-namespace --namespace jumpstarter-lab \
            --set global.baseDomain=${BASEDOMAIN} \
            --set jumpstarter-controller.grpc.endpoint=${GRPC_ENDPOINT} \
            --set jumpstarter-controller.grpc.routerEndpoint=${GRPC_ROUTER_ENDPOINT} \
            --set global.metrics.enabled=false \
            --set jumpstarter-controller.grpc.nodeport.enabled=true \
            --set jumpstarter-controller.grpc.mode=nodeport \
            --version={{controller_version}}
```

Once the Jumpstarter Service is installed in our local cluster, we can confirm that everything is working correctly by running `kubectl`.

You should see a result that looks something like this:

```{code-block} console
$ kubectl get pods -n jumpstarter-lab
NAME                                      READY   STATUS    RESTARTS        AGE
jumpstarter-controller-6cf8c4cc97-z22zl   1/1     Running   0               1m
```

### 3. Create a Local Client and Exporter

Now that the service has been deployed, let's use the Jumpstarter admin CLI to create a new local client and exporter to test our our service.

First, let's create a local client:

```{code-block} console
$ jmp admin create client my-client --save --insecure-tls-config
```

Next, let's create a local exporter:

```{code-block} console
$ jmp admin create exporter my-exporter -l type=local --save --insecure-tls-config
```

To test this out, open a new terminal window and activate your virtual environment.
Next, run the following command to start your local exporter:

```{code-block} console
$ jmp run --exporter my-exporter
```

Now, use your original terminal window to lease this exporter using the client:

```{code-block} console
$ jmp shell -l type=local
```

### 4. Provision Hardware

Now that we have successfully tested our Jumpstarter Service installation, let's provision our Raspberry Pi 3/4 device to use as an exporter.

For this tutorial, we will be using Fedora IoT as our Raspberry Pi's OS.

We chose Fedora IoT because it is an immutable OS designed specifically for containers.
This allows you to create a single image that is fully tested and deploy it at scale
to many IoT systems such as exporter hosts. Applications including the Jumpstarter Exporter
are deployed as container images allowing for easy updates at scale.

To image the Raspberry Pi, insert your micro SD card into your Linux or macOS device.

Install the `arm-image-installer` on Fedora run:

```{code-block} console
$ sudo dnf install arm-image-installer
```

Run the `lsblk` command to determine the device name of your SD card:

```{code-block} console
$ lsblk
NAME            MAJ:MIN RM   SIZE RO TYPE MOUNTPOINT
mmcblk0         179:0    0  14.9G  0 disk
├─mmcblk0p1     179:1    0   142M  0 part /run/media/user/22DA-CAE8
├─mmcblk0p2     179:2    0     1G  0 part /run/media/user/8b87a5af-12c7-4990-940e-5b457336b11f
└─mmcblk0p3     179:3    0   2.9G  0 part /run/media/user/cce2e189-9aee-4b3e-b031-aac9bdc632c9
...output omitted...
```

Download the Fedora IoT 42 image:

```{code-block} console
$ curl -LO https://download.fedoraproject.org/pub/alt/iot/42/IoT/aarch64/images/Fedora-IoT-raw-42-20250414.0.aarch64.raw.xz
```

Use the `arm-image-installer` to create the SD card:

```{code-block} console
$ sudo arm-image-installer \
    --image=<Image Path> \ # Path to the image file we just downloaded
    --target=<rpi3/rpi4> \ # Select your Raspberry Pi hardware
    --media=<SD Card Device> \ # Use the device discovered above
    --addkey=<SSH Key Path> \ # Add an SSH key to make logging in easier
    --wifi-ssid="<Wi-Fi SSID>" \ # Pre-configure your Wi-Fi network
    --wifi-pass="<Wi-Fi Password>"
```

The complete command should look like:

```{code-block} console
$ sudo arm-image-installer \
    --image=Fedora-IoT-raw-42-20250414.0.aarch64.raw.xz \
    --target=rpi4 \
    --media=/dev/mmcblk0 \
    --addkey=/home/jdoe/.ssh/id_ed25519.pub \
    --wifi-ssid="Home" \
    --wifi-pass="1234"
```

Once your SD card is imaged, you must resize the filesystem:

```{code-block} console
$ sudo arm-image-installer --media=<SD Card Device> --resizefs
```

Now, insert the SD card into your Raspberry PI and wait for it to boot.