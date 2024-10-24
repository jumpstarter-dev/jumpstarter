# Setup a Local Exporter

This guide walks you through the process of using jumpstarter with a local exporter (the client and the exporter running on the same host)

## Create Exporter Configuration
Create a text file with the following content
```yaml
# /etc/jumpstarter/exporters/demo.yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
# endpoint and token are intentionally left empty
endpoint: ""
token: ""
# mock drivers for demo purpose
export:
    storage:
        type: jumpstarter.drivers.storage.driver.MockStorageMux
    power:
        type: jumpstarter.drivers.power.driver.MockPower
```
Once the exporter configuration is ready it should be placed at `/etc/jumpstarter/exporters/demo.yaml`.

## Enter Exporter Shell
Now we can run the following command to enter the "Exporter Shell", inside which we can interact with the local exporter with jumpstarter client.
```shell
$ jmp exporter shell demo
```

## Use j Command
`j` command is available in the exporter shell for controlling the exporter with shell commands.
```shell
# running inside exporter shell
$ j
Usage: j [OPTIONS] COMMAND [ARGS]...

  Generic composite device

Options:
  --help  Show this message and exit.

Commands:
  power    Generic power
  storage  Generic storage mux
$ j power on
ok
```

## Use Python API
Or we can use the jumpstarter python API to programmatically control the exporter.

```shell
# running inside exporter shell
$ python - <<EOF
from jumpstarter.common.utils import env
with env() as client:
    print(client.power.on())
EOF
ok
```

## Exit Exporter Shell
Once you are done with the exporter, simply exit the exporter shell and the local exporter would be terminated.
