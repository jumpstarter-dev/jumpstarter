# Running tests

## Running tests through a central server

When a client configuration is present, Jumpstarter uses the specified endpoint
and token to authenticate with that server

### Configuration

By default the libraries and CLI will look for a `~/.config/jumpstarter/client.yaml`
file, which contains the endpoint and token to authenticate with the Jumpstarter
service.

Alternatively the client can receive the endpoint and token as environment variables:

```bash
export JMP_ENDPOINT=jumpstarter.my-lab.com:1443
export JMP_TOKEN=dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
```

This is useful for CI/CD systems that inject the environment variables into the pipeline.

## Running tests locally (without a server)

When no client configuration or environment variables are set, the client will
run in local mode and create an exporter instance to interact with the hardware.

Communication between the local client and exporter take place over a local
socket provided by `$JUMPSTARTER_HOST`.

```
$ jmp shell --exporter-config /exporter-config.yaml
$$ echo $JUMPSTARTER_HOST
$$ j ...
$$ exit
$
```

## Using jumpstarter from testing frameworks

### Pytest

Jumpstarter provides a pytest base class that can be used to run tests,
the base class will attempt to:

1. Use a local connection based on the `JUMPSTARTER_HOST` environment variable
2. Use an existing lease based on the `JMP_LEASE` environment variable, and existing credentials.
   See the cli reference for [jmp create lease](../cli-reference/jmp.md#jmp-create-lease).
3. Request a lease based on the `selector` provided in the test class.

```{eval-rst}
.. autoclass:: jumpstarter_testing.pytest.JumpstarterTest
    :members:
```

