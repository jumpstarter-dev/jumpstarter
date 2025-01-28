# Installation

This section contains guides to install the latest version of the Jumpstarter
components. For other versions, please select the appropriate release of this
documentation.

The Jumpstarter components which can be installed are:

| Component                                                                                                            | Description                                                                                                                                                                                                                                                                                                                                 |
| -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`jumpstarter-controller`](https://github.com/jumpstarter-dev/jumpstarter-controller)                                | The Jumpstarter Controller service, runs in k8s, manages exporters, clients, leases, and provides routing between the clients and exporters.                                                                                                                                                                                                |
| [`jumpstarter`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter)                       | The core Jumpstarter Python package. This is necessary to lease and interact with the exporters, it's also the component that runs on the exporter hosts as a service. In most cases installation is not necessary and can be consumed through another package such as `jumpstarter-cli`.                                                   |
| [`jumpstarter-cli`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-cli)               | A metapackage containing all of the Jumpstarter CLI components including the cluster admin CLI `jumpstarter-cli-admin`, the client CLI `jumpstarter-cli-client`, and exporter CLI `jumpstarter-cli-exporter`.                                                                                                                               |
| [`jumpstarter-cli-admin`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-cli-admin)   | The Jumpstarter admin CLI (`jmp-admin`). This CLI can be used to install the Jumpstarter controller, manage client/exporter registrations, and monitor/control leases.                                                                                                                                                                      |
| [`jumpstarter-cli-client`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-cli-client) | The Jumpstarter client CLI (`jmp-client`/`j`). This CLI can be used to manage local client configs, start a lease on an exporter, and enter an interactive client shell.                                                                                                                                                                    |
| [`jumpstarter-driver-*`](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages)                          | All community and official driver packages that are distributed as part of Jumpstarter are prefixed with `jumpstarter-driver-*`. This includes drivers for PySerial, SD Wire, HTTP, CAN, and more. Driver packages only need to be installed on the exporter/client if they are used by your testing environment. All drivers are optional. |


```{toctree}
service/index.md
service-cli.md
python-package.md
container-jmp.md
```


