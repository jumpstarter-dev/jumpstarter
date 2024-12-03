# Service

To manage your Jumpstarter lab from kubernetes_asyncio, the Jumpstarter Service must
be installed in your Kubernetes cluster.

## Prerequisites

- A *Kubernetes* or *OpenShift* cluster.
- [Kubectl](https://www.downloadkubernetes.com/)
- [Helm](https://helm.sh/docs/intro/install/)

```{tip}
If you don't have a Kubernetes cluster you can use a local cluster for testing,
such as `kind` or `minikube`.
```

## Installation

The service installation is very similar between targets, but there are some
specifics to each target. Please select the appropriate guide for your target:


```{toctree}
:maxdepth: 1
kubernetes-helm.md
openshift-helm.md
openshift-argocd.md
kind-helm.md
minikube-helm.md
```