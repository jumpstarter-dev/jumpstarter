# Installation

This section contains guides to install the latest version of the Jumpstarter
components. For other versions, please select the appropriate release of this
documentation.

There are two fundamental components of Jumpstarter: the [`jumpstarter` **Python
packages**](python-package.md) and the optional [**service**
`jumpstarter-controller`](service/index.md).

The **service** is an optional component that runs in
[Kubernetes](https://kubernetes.io) which makes sharing exporters easier in a
lab environment, while the Python packages are necessary to set up and interact with exporters,
and also provide a helpful set of tools to manage the service.

```{toctree}
python-package.md
service/index.md
```
