# Python Packages and CLI

## Release install

The [Jumpstarter Python packages](https://jumpstarter.dev/packages/)
contain all the necessary tools to run an exporter or interact with your
hardware as a client.

The Python package can be installed using ``pip`` or similar. Python
{{requires_python}} is required:

```shell
$ pip3 install --extra-index-url https://jumpstarter.dev/packages/simple jumpstarter-all

$ mkdir -p "${HOME}/.config/jumpstarter/"
$ sudo mkdir /etc/jumpstarter
```

```{tip}
This will install the `jumpstarter` packages globally. When using Jumpstarter
as a library, it is recommended to install the package in a virtual environment
instead:

$ python3 -m venv ~/.venv/jumpstarter

$ source ~/.venv/jumpstarter/bin/activate

$ pip3 install ....
```

An alternative to installing the packages is to [use the container
package](#running-in-a-container).

## Development install
Jumpstarter is under active development, and new features are added frequently.
We perform basic e2e testing and thorough unit testing, so we recommend
installing the latest version from the `main` branch.

For this, you will need a few tools like `uv`, `make`, and `git`:
```shell
$ sudo dnf install -y uv make git

# Clone the repository
$ git clone https://github.com/jumpstarter-dev/jumpstarter.git
$ cd jumpstarter

$ rm .python-version # remove the Python version pinning

$ make # creates the dist directory with all the packages
$ make sync # installs the packages in a local .venv

# create the configuration directories
$ mkdir -p "${HOME}/.config/jumpstarter/"
$ sudo mkdir /etc/jumpstarter

```

Then you can use the Jumpstarter CLI commands by activating the Python virtual
environment:
```shell
$ source .venv/bin/activate
$ jmp version
```

```{tip}
If you configured a jmp alias to use the container,
please undefine those aliases before running the `jmp` command.

i.e. `unalias jmp`
```


## Running in a Container

For interacting with the Service without installing the Python
packages locally, you can create an alias to run the `jmp` client in a
container.

```{tip}
It is recommended to add the alias to your shell profile.
```

```{code-block} bash
:substitutions:
$ alias jmp='podman run --rm -it -w /home \
               -v "$(pwd):/home":z \
              -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
               quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp'
```

Then you can try:

```shell
$ jmp config client list
CURRENT   NAME      ENDPOINT                         PATH
*         default   grpc.devel.jumpstarter.dev:443   /root/.config/jumpstarter/clients/default.yaml
          test      grpc.devel.jumpstarter.dev:443   /root/.config/jumpstarter/clients/test.yaml
```

### Hardware Access for Exporters

If you need access to your hardware, e.g., because you are running the `jmp`
command or you are following the [local-only
workflow](../architecture.md#local-mode) (i.e., without a distributed service),
you need to mount access to devices into the container, provide host network
access, and run the container in privileged mode. This will probably need to be run
as **root**.


```{code-block} bash
:substitutions:
$ mkdir -p "${HOME}/.config/jumpstarter/" /etc/jumpstarter

# you may want to add this alias to the profile
$ alias jmp='podman run --rm -it \
              -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
              --net=host --privileged \
              -v /run/udev:/run/udev -v /dev:/dev -v /etc/jumpstarter:/etc/jumpstarter:z \
              quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp'
```

## Python Components


The Jumpstarter packages which can be installed are:

| Component                                                                                                          | Description                                                                                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`jumpstarter`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter)                     | The core Jumpstarter Python package. This is necessary to lease and interact with the exporters; it's also the component that runs on the exporter hosts as a service. In most cases, installation is not necessary and can be consumed through another package such as `jumpstarter-cli`.                                                  |
| [`jumpstarter-cli`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-cli)             | A metapackage containing all of the Jumpstarter CLI components including the cluster admin CLI `jumpstarter-cli-admin` and the user-facing CLI.                                                                                                                                                                                             |
| [`jumpstarter-cli-admin`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-cli-admin) | The Jumpstarter admin CLI (`jmp-admin`). This CLI can be used to install the Jumpstarter controller, manage client/exporter registrations, and monitor/control leases.                                                                                                                                                                      |
| [`jumpstarter-driver-*`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages)                        | All community and official driver packages that are distributed as part of Jumpstarter are prefixed with `jumpstarter-driver-*`. This includes drivers for PySerial, SD Wire, HTTP, CAN, and more. Driver packages only need to be installed on the exporter/client if they are used by your testing environment. All drivers are optional. |
| [`jumpstarter-adapter-*`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages)                       | All community and official adapter packages that are distributed as part of Jumpstarter are prefixed with `jumpstarter-adapter-*`. This includes adapters to redirect streams to local ports, unix sockets, perform SSH connections, etc.                                                                                                   |
| [`jumpstarter-imagehash`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-imagehash) | A library to perform image checking from video inputs using the simple Python imagehash library                                                                                                                                                                                                                                             |
| [`jumpstarter-testing`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-testing)     | Testing tools for writing Jumpstarter-powered tests with `pytest`.                                                                                                                                                                                                                                                                          |
