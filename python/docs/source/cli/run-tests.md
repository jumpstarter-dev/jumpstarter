# Run Tests

## Running tests through a central server

When client configuration exists, Jumpstarter will use the specified endpoint
and token to authenticate with that server

### Configuration

By default the libraries and CLI will look for a `~/.config/jumpstarter/client.yaml`
file, which contains the endpoint and token to authenticate with the Jumpstarter
service.

Alternatively the client can receive the endpoint and token as environment variables:

```bash
export JUMPSTARTER_ENDPOINT=grpcs://jumpstarter.my-lab.com:1443
export JUMPSTARTER_TOKEN=dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
```

This is useful for CI/CD systems that inject the environment variables into the pipeline.

A custom location to the client configuration can also be passed using the following
environment variable. This is useful in situations where there are multiple client
configurations or in an environment when the config is mounted as a file.

```bash
export JUMPSTARTER_CONFIG=/etc/jumpstarter/my-client.yaml
```

## Running tests locally (without a server)

When no client configuration or environment variables are set, the client will
run in local mode and create an exporter instance to interact with the hardware.

Communication between the local client and exporter take place over a local
socket: `/var/run/jumpstarter.sock`.

A local instance of the exporter can also be started using the following command:

```bash
systemctl start jumpstarter-exporter
```
