# Jumpstarter Controller

The Jumpstarter Controller is a Kubernetes [controller](https://github.com/jumpstarter-dev/jumpstarter-controller)
which helps manage your clients and exporters from within any Kubernetes cluster.

The Controller keeps track of the connected clients and exporters using Kubernetes
[Custom Resource Definitions (CRDs)](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/),
this means that your entire hardware testing setup can be easily managed using 
GitOps tools like [ArgoCD](https://argoproj.github.io/cd/).

## Prerequisites

```{note}
See the documentation on [setting up a local cluster](./local-cluster.md) if you
want to install the Controller on your local machine.
```

- A [Kubernetes cluster](https://www.downloadkubernetes.com/).
- [Kubectl](https://www.downloadkubernetes.com/).
- [Helm](https://helm.sh/docs/intro/install/) - we use Helm to install the CRDs.

## Installation

To install the Jumpstarter Controller on a Kubernetes cluster:

1. 