# Jumpstarter Service

To manage your Jumpstarter lab from Kubernetes, the Jumpstarter Service must
be installed in your Kubernetes cluster.

## Prerequisites

```{tip}
See the documentation on [setting up a local cluster](./local-cluster.md) if you
want to install the Controller on your local machine.
```

- A [Kubernetes cluster](https://www.downloadkubernetes.com/)
- [Kubectl](https://www.downloadkubernetes.com/)
- [Helm](https://helm.sh/docs/intro/install/)

## Installation

To install the Jumpstarter Service on a Kubernetes cluster using Helm run:

```bash
$ helm install oci://quay.io/jumpstarter-dev/helm/jumpstarter --create-namespace \
    --namespace jumpstarter-lab \
    --set global.baseDomain=<BASE_DOMAIN> \
    --set jumpstarter-controller.grpc.route.enabled=true \
    --set jumpstarter-controller.grpc.tls.enabled=true 
```

```{note}
Please replace `<BASE_DOMAIN>` with the hostname you would like to use for your
Jumpstarter endpoint.

- If you're using kind, you can use the [`extraPortMapping`](https://kind.sigs.k8s.io/docs/user/ingress/#create-cluster) 
config option to map the Jumpstarter ingress to `localhost`.

- If you're using minikube, you can [add an entry to `/etc/hosts`](https://kubernetes.io/docs/tasks/access-application-cluster/ingress-minikube/#test-your-ingress) 
so you can resolve the hostname assigned to Jumpstarter.
```
