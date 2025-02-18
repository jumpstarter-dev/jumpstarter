# Installation

This section contains guides to install the latest version of the Jumpstarter
components. For other versions, please select the appropriate release of this
documentation.

There are 2 fundamental components of Jumpstarter, the [`jumpstarter` **python packages**](python-package.md)
and the optional [**service** `jumpstarter-controller`](service/index.md).

The **service** is an optional component that runs in [Kubernetes](https://kubernetes.io) which makes sharing exporters
easier in a lab, while the Python packages are necessary to setup and interact
exporters, and also provide a very helpful set of tools to manage the service.

```{toctree}
python-package.md
service/index.md
```
