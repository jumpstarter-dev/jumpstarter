# Using UV Package Manager

The Jumpstarter framework uses the `uv` package manager and a monorepo structure to manage multiple Python packages.

## Monorepo Structure

### Package Directories

Our `uv` monorepo is organized into `packages` and `examples` directories.

Each Jumpstarter package is contained within `packages` using a kebab case naming.

Package directory names follow specific naming conventions:

- The core package is called `jumpstarter`
- Driver packages are prefixed with `jumpstarter-driver-*`
- CLI tool packages are prefixed with `jumpstarter-cli-*`
- Utility packages are prefixed with `jumpstarter-*`
- Other packages such as `hatci-pin-jumpstarter` can have any name as long as it is kebab case.

### Package Structure

Within each package directory we have the following files/directories:

- The package module directory in snake case, e.g. `jumpstarter_driver_dutlink`
- `pyproject.toml` - The specific config for this package.
- `README.md` - A README with specific information on this driver/package.

### Root `pyproject.toml`

The root `pyproject.toml` file configures the `uv` workspace, includes package sources, and sets documentation (`docs` group) and development (`dev` group) dependencies as well as global tool configuration.

All packages in the workspace should be included in the `tool.uv.sources` section. For example, if a package called `jumpstarter-driver-example` is created, the following line must be added to `tool.uv.sources` in filesystem order:

```toml
[tool.uv.sources]
# ... Other sources
jumpstarter-driver-example = { workspace = true }
# ... Other sources
```

### Package `pyproject.toml`

Each package must have its own `pyproject.toml` file and define its own dependencies and specific tool configurations (if required).

Here is an example `pyproject.toml`:

```toml
# packages/jumpstarter-driver-shell/pyproject.toml
[project]
name = "jumpstarter-driver-shell"
dynamic = ["version", "urls"]
description = "Jumpstarter shell driver, for running controlled shell commands on the exporter."
readme = "README.md"
authors = [{ name = "Miguel Angel Ajo", email = "miguelangel@ajo.es" }]
requires-python = ">=3.11"
license = "Apache-2.0"
dependencies = ["anyio>=4.6.2.post1", "jumpstarter"]

[project.entry-points."jumpstarter.drivers"]
Shell = "jumpstarter_driver_shell.driver:Shell"

[tool.pytest.ini_options]
addopts = "--cov --cov-report=html --cov-report=xml"
log_cli = true
log_cli_level = "INFO"
testpaths = ["jumpstarter_driver_shell"]

[dependency-groups]
dev = ["pytest-cov>=6.0.0", "pytest>=8.3.3"]

[tool.hatch.metadata.hooks.vcs.urls]
Homepage = "https://jumpstarter.dev"
source_archive = "https://github.com/jumpstarter-dev/repo/archive/{commit_hash}.zip"

[tool.hatch.version]
source = "vcs"
raw-options = { 'root' = '../../' }

[build-system]
requires = ["hatchling", "hatch-vcs", "hatch-pin-jumpstarter"]
build-backend = "hatchling.build"

[tool.hatch.build.hooks.pin_jumpstarter]
name = "pin_jumpstarter"
```

#### Driver Entry Points

To register Jumpstarter drivers, we use the `project.entry-points."jumpstarter.drivers"` and `project.entry-points."jumpstarter.adapters"` to define entry point classes for Jumpstarter drivers and adapters.

## Basic Commands

Many `uv` commands have been abstracted into our `Makefile`.

Here are some useful `make` commands:

- `make sync` - Runs a `uv` sync for all packages and all extras.
- `make build` - Runs a `uv` build for all packages and outputs to `dist/`.
- `make clean-venv` - Deletes the `.venv/` directory and all `__pycache__` directories.
- `make clean-build` - Deletes the `dist/` directory.
- `make clean` - Cleans docs, venv, build, and tests.

To run the Jumpstarter CLI tool with `uv`, use `uv run jmp`.

## Creating Driver Packages

To create a new driver package, use the `make create-driver` template command:

```bash
make create-driver DRIVER_NAME=my_driver DRIVER_CLASS=MyDriver
```

This will prompt you with defaults from your git config:

- Author name (default: your git config user.name)
- Author email (default: your git config user.email)
