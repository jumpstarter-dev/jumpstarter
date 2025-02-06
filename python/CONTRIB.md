# contrib directory
This directory contains extensions to the core codebase, community drivers and adapter libraries and tools.

If you are working on a driver, or adapter library of general interest,
please consider submitting it to this repository, as it will become available
to all jumpstarter users.

If you are creating a driver or adapter library for a specific need, not of general
interest, or that needs to be private, please consider forking our [jumpstarter-driver-template](https://github.com/jumpstarter-dev/jumpstarter-driver-template)


## Creating a new drivers scaffold
To create a new driver scaffold, you can use the `create_driver.sh` script in this directory. This should help you star a new driver project with the right structure and dependencies quickly.

From the root directory of the project, run the following command:
```shell
$ ./__templates__/create_driver.sh yepkit Ykush "Miguel Angel Ajo" "miguelangel@ajo.es"

Creating: packages/jumpstarter_driver_yepkit/jumpstarter_driver_yepkit/__init__.py
Creating: packages/jumpstarter_driver_yepkit/jumpstarter_driver_yepkit/client.py
Creating: packages/jumpstarter_driver_yepkit/jumpstarter_driver_yepkit/driver_test.py
Creating: packages/jumpstarter_driver_yepkit/jumpstarter_driver_yepkit/driver.py
Creating: packages/jumpstarter_driver_yepkit/.gitignore
Creating: packages/jumpstarter_driver_yepkit/pyproject.toml
Creating: packages/jumpstarter_driver_yepkit/README.md
Creating: packages/jumpstarter_driver_yepkit/examples/exporter.yaml

$ make sync
uv sync --all-packages --all-extras
Resolved 125 packages in 18ms
   Built jumpstarter-driver-shell @ file:///Users/ajo/work/jumpstarter/contrib/drivers/shell
Prepared 1 package in 569ms
Uninstalled 1 package in 1ms
Installed 1 package in 1ms
 ~ jumpstarter-driver-shell==0.1.0 (from file:///Users/ajo/work/jumpstarter/contrib/drivers/shell)
````
