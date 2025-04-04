# Reference

This section provides details on the Jumpstarter CLI.

## jmp

The base jmp command contains a set of subcommands for the different features, those
can also be installed and used independently as `jmp-admin` and through other CLI packages.

```bash
jmp [OPTIONS] COMMAND [ARGS]...
```

### commands

The `jmp-admin` or `jmp admin` CLI allows administration of exporters and clients in a Kubernetes cluster. To use this CLI, you must have a valid `kubeconfig` and access to the cluster/namespace where the Jumpstarter controller resides.

```{toctree}
:maxdepth: 1
jmp-admin.md
```

The `jmp` CLI allows interaction with Jumpstarter as a client or exporter.

```{toctree}
:maxdepth: 1
jmp.md
```
