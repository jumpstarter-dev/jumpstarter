# Service Installation

This section explains how to install and configure the Service in your Kubernetes cluster. The service enables centralized management of your Jumpstarter lab environment. You'll need a Kubernetes cluster, kubectl, and Helm to proceed with installation.

The following pages will guide you through service installation for different environments:

* **[Kubernetes with Helm](kubernetes-helm.md)** - Standard Kubernetes installation using Helm
* **[OpenShift with Helm](openshift-helm.md)** - Installation on OpenShift using Helm
* **[OpenShift with ArgoCD](openshift-argocd.md)** - GitOps-based installation using ArgoCD
* **[Kind with Helm](kind-helm.md)** - Installation on a local Kind cluster
* **[Minikube with Helm](minikube-helm.md)** - Installation on Minikube

```{toctree}
:maxdepth: 1
:hidden:
kubernetes-helm.md
openshift-helm.md
openshift-argocd.md
kind-helm.md
minikube-helm.md
```
