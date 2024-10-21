# OpenShift (Helm)
```{tip}
Please note that `global.baseDomain` is used to create the host names for the services,
with the provided example the services will be available at grpc.jumpstarter.example.com
and router.jumpstarter.example.com.
````

```{note}
Please note that you will need administrator access to the cluster to install the Jumpstarter Service,
this is because the install process will install some CRDs and ClusterRoles.
```

To install using helm:

```bash
  helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
              --create-namespace --namespace jumpstarter-lab \
              --set global.baseDomain=jumpstarter.example.com \
              --set global.metrics.enabled=true \
              --set jumpstarter-controller.grpc.mode=route \
              --version=0.1.0
```


