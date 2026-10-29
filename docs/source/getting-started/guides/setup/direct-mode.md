# Direct Mode

This guide shows you how to run a Jumpstarter {term}`exporter` that clients connect to
directly over TCP - no {term}`controller` or Kubernetes cluster required.

{term}`Direct mode <direct mode>` is useful when you want to expose hardware on one machine to clients
on another, without setting up a {term}`controller`.

```{note}
{term}`Direct mode` skips the {term}`controller`'s {term}`lease` management. Only one client should
connect at a time. For shared, multi-user environments use
[distributed mode](distributed-mode.md) instead.
```

## Instructions

### Create an Exporter Configuration

Unlike {term}`distributed mode`, you don't need `endpoint` or `token` fields - there
is no {term}`controller` to register with.

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

The {term}`hook`s section is optional. `beforeLease` runs once during exporter
startup. Eligible `afterLease` cleanup runs once when a client calls `EndSession`
or the exporter shuts down. {term}`Hook` scripts can use {term}`j` commands to
interact with the drivers.

Direct mode uses the same lifecycle states as controller mode, with one session
for the process lifetime. EndSession retains the server for final status and logs
until the exporter stops; it does not start another hook cycle for a new client.
Stop requests, including drain signals, finish the running hook and eligible
cleanup before closing the session, subject to configured hook timeouts.

A hook failure with `onFailure: endLease` keeps the failed session until stop;
`onFailure: exit` stops the exporter with code `1`. Either policy skips the
after-hook when the before-hook fails. See [Hooks](../../../introduction/hooks.md)
for configuration and failure handling.

### Start the Exporter

Run the {term}`exporter` and tell it to listen on a TCP port with `--tls-grpc-listener`:

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

If the {term}`exporter` requires a passphrase:

```console
$ jmp shell --tls-grpc <HOST>:19090 --tls-grpc-insecure --passphrase my-secret
```

Replace `<HOST>` with the {term}`exporter` machine's IP address or hostname. Once
connected, interact with the {term}`exporter` using `j` commands:

```console
$ j power on
$ j power off
```

You can also pass a command directly without opening an interactive shell:

```console
$ jmp shell --tls-grpc <HOST>:19090 --tls-grpc-insecure -- j power on
```
