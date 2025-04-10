# OpenShift (Helm)
```{tip}
Please note that `global.baseDomain` is used to create the hostnames for the services.
With the provided example, the services will be available at grpc.jumpstarter.example.com
and router.jumpstarter.example.com.
````

```{note}
Please note that you will need administrator access to the cluster to install the Service,
as the installation process will install CRDs and ClusterRoles.
```

To install using Helm:

```{code-block} bash
:substitutions:
helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
          --create-namespace --namespace jumpstarter-lab \
          --set global.baseDomain=jumpstarter.example.com \
          --set global.metrics.enabled=true \
          --set jumpstarter-controller.grpc.mode=route \
          --version={{controller_version}}
```


