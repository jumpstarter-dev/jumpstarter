# Service CLI

The distributed service CLI (jmpctl) is a command line interface that enables easy administration
of your Exporters and Clients.

Please refer to [the latest release page](https://github.com/jumpstarter-dev/jumpstarter-controller/releases/latest) for
the latest version.

## Installing the CLI
```{code-block} bash
:substitutions:
export VERSION={{controller_version}}
export ARCH=amd64 # or arm64 (Apple Silicon)
export PLATFORM=linux # or darwin for macOS

curl -L https://github.com/jumpstarter-dev/jumpstarter-controller/releases/download/v${VERSION}/jmpctl_${VERSION}_${PLATFORM}_${ARCH} -o jmpctl
sudo install jmpctl /usr/local/bin/jmpctl && rm jmpctl
```

## Configuration
The `jmpctl` CLI requires a kubeconfig file with permissions to access the jumpstarter installation.

`jumpstarter-lab` is the default if you followed the previous install sections.


## Usage
```bash
$ jmpctl
Admin CLI for managing jumpstarter

Usage:
  jmpctl [command]

Available Commands:
  client      Manage clients
  completion  Generate the autocompletion script for the specified shell
  exporter    Manage exporters
  help        Help about any command

Flags:
  -h, --help                help for jmpctl
      --kubeconfig string   Path to the kubeconfig file to use
      --namespace string    Kubernetes namespace to operate on (default "default")
      --timeout string      command timeout (default "10s")

Use "jmpctl [command] --help" for more information about a command.

```

