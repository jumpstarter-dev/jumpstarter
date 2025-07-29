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

### Installing Packages

The [Jumpstarter Python packages](https://pkg.jumpstarter.dev/) provide all the
tools you need to interact with hardware locally.

#### Using the Installer
##### Quick Install (Recommended)

Install directly using our installer from github:

For the latest release:
```{code-block} console
curl -fsSL https://raw.githubusercontent.com/jumpstarter-dev/jumpstarter/main/install.sh | bash
```

Or with explicit source specification (main branch example)
```{code-block} console
curl -fsSL https://raw.githubusercontent.com/jumpstarter-dev/jumpstarter/main/install.sh | bash -s -- -s main
```

##### Local Installation

If you have the repository cloned locally:

| Installation Type | Command | Description |
|------------------|---------|-------------|
| Stable release (recommended) | `./install.sh` | Install stable release 0.6 |
| Stable release (explicit) | `./install.sh -s release-0.6` | Install stable release 0.6 explicitly |
| Development version | `./install.sh -s main` | Install latest development version |
| Release candidate | `./install.sh -s rc` | Install latest release candidate (when available) |
| Custom directory | `./install.sh -d /opt/jumpstarter` | Install to custom directory |

##### Installation Directory Structure

After installation, the following structure is created:

```
~/.local/jumpstarter/
├── venv/                    # Python virtual environment
├── bin/                     # Command symlinks
│   ├── jmp
│   └── j
└── set                      # Environment activation script
```

##### Activating the Environment

Activate for current session, adjust ~/.local/jumpstarter if you picked a custom install directory.
```{code-block} console
source ~/.local/jumpstarter/set
```

Or add to your shell profile for permanent activation, adjust ~/.local/jumpstarter if you picked a custom install directory.
```{code-block} console
echo 'source ~/.local/jumpstarter/set' >> ~/.bashrc
```

##### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `-s, --source SOURCE` | Installation source (release-0.6, latest, rc, main) | `release-0.6` |
| `-d, --dir DIR` | Installation directory | `~/.local/jumpstarter` |
| `-h, --help` | Show help message | - |

##### Prerequisites

- Python 3.11 or higher
- pip3
- bash shell


##### Uninstalling

To completely remove Jumpstarter, adjust if you picked a custom install directory.

```{code-block} console
# Remove installation directory
rm -rf ~/.local/jumpstarter

# Remove from shell profile (if added)
# Edit ~/.bashrc, ~/.zshrc, etc. and remove the source line
```

#### Using pip

##### Prerequisites

- Python {{requires_python}}
- A package manager such as [`pip`](https://pip.pypa.io/en/stable/installation/) or [`uv`](https://docs.astral.sh/uv/getting-started/installation).

To install all the Jumpstarter core packages and drivers, use the `jumpstarter-all` meta package:

```{tip}
Consider installing your Python packages in a [virtual environment](https://docs.python.org/3/library/venv.html) instead of globally.
```

````{tab} Global
```{code-block} console
:substitutions:
# Install with pip
$ pip3 install --extra-index-url {{index_url}} jumpstarter-all

# Create config directories for Jumpstarter
$ mkdir -p "${HOME}/.config/jumpstarter/"

# Create the exporter config directory
$ sudo mkdir /etc/jumpstarter
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

# Create the exporter config directory
$ sudo mkdir /etc/jumpstarter
```
````

````{tab} uv
```{code-block} console
:substitutions:
# Create a new virtual environment
$ uv venv

# Install with uv
$ uv add --extra-index-url {{index_url}} jumpstarter-all

# Create config directories for Jumpstarter
$ mkdir -p "${HOME}/.config/jumpstarter/"

# Create the exporter config directory
$ sudo mkdir /etc/jumpstarter
```
````

Additional package indexes are available, this is a complete list of our
indexes:

| Index                                                  | Description                                                           |
| ------------------------------------------------------ | --------------------------------------------------------------------- |
| [releases](https://pkg.jumpstarter.dev/)               | Release, or release-candidate versions                                |
| [main](https://pkg.jumpstarter.dev/main/)              | Index tracking the main branch, equivalent to installing from sources |
| [release-0.6](https://pkg.jumpstarter.dev/release-0.6) | Index tracking a stable branch                                        |

#### Installing from Source

Jumpstarter is in active development with frequent feature additions. We
conduct thorough testing and recommend installing the latest version from the
`main` branch.

##### Prerequisites

- [`uv`](https://docs.astral.sh/uv/getting-started/installation) - A modern Python package manager for monorepos.
- `make` - The make build tool.
- `git` - Clone the repository with Git.

Run the following commands to clone the repository and create a virtual environment:

```{code-block} console
# Clone the git repository
$ git clone https://github.com/jumpstarter-dev/jumpstarter.git

# Open Jumpstarter
jumpstarter$ cd jumpstarter

# Install Python venv and sync packages with uv
jumpstarter$ make sync

# Create local config directories for Jumpstarter
$ mkdir -p "${HOME}/.config/jumpstarter/"

# Create the exporter config directory
$ sudo mkdir /etc/jumpstarter

# Activate the virtual environment to use the Jumpstarter CLI
$ source .venv/bin/activate
$ jmp version
```

#### Run in a Container

If you prefer not to install packages locally, you can run Jumpstarter from a container using [Docker](https://docker.com) or [Podman](https://podman.io).

First, create the config directories so you can mount them inside the container:

```{code-block} console
# Create local config directories for Jumpstarter
$ mkdir -p "${HOME}/.config/jumpstarter/"

# Create the exporter config directory
$ sudo mkdir /etc/jumpstarter
```

To start a Jumpstarter container with all the driver packages pre-installed, run:

````{tab} Podman
```{code-block} console
:substitutions:
$ podman run --rm -it \
    -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
    quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp
```
````

````{tab} Docker
```{code-block} console
:substitutions:
$ docker run --rm -it \
    -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
    quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp
```
````

To interact with Jumpstarter without local Python package installation,
create an alias to run the `jmp` client in a container.

We recommend adding this alias to your shell profile (`~/.bashrc` or `~/.zshrc`) for persistent use:

````{tab} Podman
```{code-block} console
:substitutions:
$ alias jmp='podman run --rm -it -w /home \
    -v "$(pwd):/home":z \
    -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
    quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp'
```
````

````{tab} Docker
```{code-block} console
:substitutions:
$ alias jmp='docker run --rm -it -w /home \
    -v "$(pwd):/home":z \
    -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
    quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp'
```
````

If you've configured a `jmp` alias you can undefine it with:

```console
$ unalias jmp
```

When you need hardware access for running the `jmp` command or following the
[local-only workflow](../../introduction/index.md#local-mode), configure the
container with device access, host networking, and privileged mode. This
typically requires `root` privileges:

````{tab} Podman
```{code-block} console
:substitutions:
$ sudo podman run --rm -it \
    -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
    --net=host --privileged \
    -v /run/udev:/run/udev -v /dev:/dev -v /etc/jumpstarter:/etc/jumpstarter:z \
    quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp
```
````

````{tab} Docker
```{code-block} console
:substitutions:
$ sudo docker run --rm -it \
    -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter":z \
    --net=host --privileged \
    -v /run/udev:/run/udev -v /dev:/dev -v /etc/jumpstarter:/etc/jumpstarter:z \
    quay.io/jumpstarter-dev/jumpstarter:{{version}} jmp
```
````