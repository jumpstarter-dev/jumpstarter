# Packages

## Python

Jumpstarter includes the following installable Python packages:

- `jumpstarter`: Core package for exporter interaction and service hosting
- `jumpstarter-cli`: CLI components metapackage including admin and user
  interfaces
- `jumpstarter-cli-admin`: Admin CLI for controller management and lease control
- `jumpstarter-driver-*`: Drivers for device connectivity
- `jumpstarter-imagehash`: Image checking library for video inputs
- `jumpstarter-testing`: Tools for Jumpstarter-powered pytest integration

### Installing Release Packages

The [Jumpstarter Python packages](https://pkg.jumpstarter.dev/) provide all the
tools you need to run an exporter or interact with your hardware as a client.

Install the Python package using `pip` or a similar tool. You need Python
{{requires_python}}:

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-all
$ mkdir -p "${HOME}/.config/jumpstarter/"
$ sudo mkdir /etc/jumpstarter
```

The command above installs packages globally. For library usage, we recommend
installing in a virtual environment instead:

```{code-block} console
:substitutions:
$ python3 -m venv ~/.venv/jumpstarter
$ source ~/.venv/jumpstarter/bin/activate
$ pip3 install --extra-index-url {{index_url}} jumpstarter-all
```

Additional package indexes are available, this is a complete list of our
indexes:

| Index | Description |
|-------|-------------|
| [releases](https://pkg.jumpstarter.dev/) | Release, or release-candidate versions |
| [main](https://pkg.jumpstarter.dev/main/) | Index tracking the main branch, equivalent to installing from sources |
| [release-0.6](https://pkg.jumpstarter.dev/release-0.6) | Index tracking a stable branch |

### Installing from Source

Jumpstarter undergoes active development with frequent feature additions. We
conduct thorough testing and recommend installing the latest version from the
`main` branch.

```console
$ sudo dnf install -y uv make git
$ git clone https://github.com/jumpstarter-dev/jumpstarter.git
$ cd jumpstarter
$ rm .python-version
$ make sync
$ mkdir -p "${HOME}/.config/jumpstarter/"
$ sudo mkdir /etc/jumpstarter
```

Activate the virtual environment to use Jumpstarter CLI commands:

```console
$ source .venv/bin/activate
$ jmp version
```

### Running in a Container

If you prefer not to install packages locally, you can use the container package
instead. To interact with the service without local Python package installation,
create an alias to run the `jmp` client in a container. We recommend adding this
alias to your shell profile for persistent use:

```{code-block} console
:substitutions:
$ alias jmp='podman run --rm -it -w /home \
    -v "$(pwd):/home":z \
    -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
    quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp'
```

When you need hardware access for running the `jmp` command or following the
[local-only workflow](../../introduction/index.md#local-mode), configure the
container with device access, host networking, and privileged mode. This
typically requires `root` privileges:

```{code-block} console
:substitutions:
$ mkdir -p "${HOME}/.config/jumpstarter/" /etc/jumpstarter
$ alias jmp='podman run --rm -it \
    -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
    --net=host --privileged \
    -v /run/udev:/run/udev -v /dev:/dev -v /etc/jumpstarter:/etc/jumpstarter:z \
    quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp'
```

If you've configured a `jmp` alias you can undefine it with:

```console
$ unalias jmp
```
