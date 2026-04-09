# Setup Direct Mode

This guide shows you how to run a Jumpstarter exporter that clients connect to
directly over TCP — no controller or Kubernetes cluster required.

Direct mode is useful when you want to expose hardware on one machine to clients
on another, without setting up a controller.

```{note}
Direct mode skips the controller's lease management. Only one client should
connect at a time. For shared, multi-user environments use
[distributed mode](setup-distributed-mode.md) instead.
```

## Instructions

### Create an Exporter Configuration

Unlike distributed mode, you don't need `endpoint` or `token` fields — there
is no controller to register with.

Create `example-direct.yaml`:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: example-direct
export:
  power:
    type: jumpstarter_driver_power.driver.MockPower
hooks:
  beforeLease:
    script: |
      echo "Exporter ready"
      j power on
    timeout: 30
  afterLease:
    script: |
      j power off
    timeout: 30
```

The `hooks` section is optional. `beforeLease` runs once when the exporter
starts (before any client connects), and `afterLease` runs on shutdown. Hook
scripts can use `j` commands to interact with the drivers.

### Start the Exporter

Run the exporter and tell it to listen on a TCP port with `--tls-grpc-listener`:

```console
$ jmp run --exporter-config example-direct.yaml \
    --tls-grpc-listener 0.0.0.0:19090 \
    --tls-grpc-insecure
```

The `--tls-grpc-insecure` flag disables TLS, which is convenient for local
development. For production use, provide `--tls-cert` and `--tls-key` instead.

To require a passphrase from connecting clients, add `--passphrase`:

```console
$ jmp run --exporter-config example-direct.yaml \
    --tls-grpc-listener 0.0.0.0:19090 \
    --tls-grpc-insecure \
    --passphrase my-secret
```

### Connect a Client

```console
$ jmp shell --tls-grpc <HOST>:19090 --tls-grpc-insecure
```

If the exporter requires a passphrase:

```console
$ jmp shell --tls-grpc <HOST>:19090 --tls-grpc-insecure --passphrase my-secret
```

Replace `<HOST>` with the exporter machine's IP address or hostname. Once
connected, interact with the exporter using `j` commands:

```console
$ j power on
$ j power off
```

You can also pass a command directly without opening an interactive shell:

```console
$ jmp shell --tls-grpc <HOST>:19090 --tls-grpc-insecure -- j power on
```
