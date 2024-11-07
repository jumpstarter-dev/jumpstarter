# Installation

This section contains guides to install the latest version of the Jumpstarter
components. For other versions, please select the appropriate release of this
documentation.

The Jumpstarter components which can be installed are:

| Component                 | Description                                                   |
| ---------                 | -----------                                                   |
| jumpstarter-controller    | The Jumpstarter Controller service, runs in k8s, manages exporters, clients, leases,  and provides routing |
| jmpctl                    | The Jumpstarter administrator CLI tool, simplifies the administrator experience when managing clients or exporters on the controller |
| jmp /jmp-exporter / j / python packages | The Jumpstarter Python package. This is necessary to lease and interact with the exporters, it's also the component ran on the exporter hosts as a service. In most cases installation is not necessary and can be consumed through a container.  |


```{toctree}
service/index.md
service-cli.md
python-package.md
container-jmp.md
```


