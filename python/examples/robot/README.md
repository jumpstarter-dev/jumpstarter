# Robot Framework Example

This example uses [`jumpstarter-robot`](../../packages/jumpstarter-robot) to
control Jumpstarter drivers from [Robot Framework](https://robotframework.org/).
It uses mock drivers. The test run starts these drivers in its own process
(local mode). Hardware and a controller are not necessary.

The [Robot Framework guide](../../../docs/source/getting-started/guides/robot-framework.md)
explains this example.

## Run the tests

1. Install the workspace one time. Do this in the `python/` directory:

   ```console
   $ uv sync --all-packages --all-extras
   ```

2. Run the tests:

   ```console
   $ cd python/examples/robot
   $ uv run robot -d results tests/
   ```

To run the tests on a lab exporter that has the same drivers, use a lease:

```console
$ jmp shell --selector board=mock -- robot -d results tests/
```

## Files

| Path | Contents |
| --- | --- |
| `exporter.yaml` | The mock board: a `power` switch, a `storage` mux, a `console` byte stream that sends back what it receives, and a `shell` driver with the methods `hello` and `fail`. |
| `robot.toml` | The RobotCode settings and the client settings for the generator. |
| `client.resource` | The client resource that the generator made. It has one keyword library for each driver. |
| `tests/power.robot` | Power keywords, stream values, and `Call Driver`. |
| `tests/storage.robot` | Writes `data/image.bin` through the storage mux. |
| `tests/shell.robot` | The shell methods `Shell Hello` and `Shell Fail`, and their return codes. |
| `tests/console.robot` | The Telnet library of Robot Framework on the console, through `Open Port Forward`. |

## Make the client resource again

The generator makes `client.resource` from `exporter.yaml` and the settings in
`robot.toml`. The keyword names start with the path of the driver: `power`
gives `Power On`, and `storage` gives `Storage Flash`.

If you change one of these two files:

1. Run the generator in this directory:

   ```console
   $ uv run jmp-robot generate
   ```

2. Commit `client.resource`.
