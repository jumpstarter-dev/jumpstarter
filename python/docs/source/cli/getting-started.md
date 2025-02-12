# Getting Started

To simplify the management and operation of Jumpstarter, we provide several command-line tools for different use cases. These CLI tools can also be installed together through the `jmpstarter-cli` package and accessed using the `jmp` command for simplicity.

Each tool can also be installed separately for users who want to reduce the dependencies on their developer machine or an embedded exporter with limited available resources.

## Admin CLI `jmp-admin`

The `jmp-admin` or `jmp admin` CLI allows administration of exporters and clients
in a Kubernetes cluster. To use this CLI, you must have a valid `kubeconfig` and
access to the cluter/namespace where the Jumpstarter controller resides.

## Client CLI `jmp-client`

The `jmp-client` or `jmp client` CLI allows interaction with Jumpstarter clients
and the management of client configs.

## Exporter CLI `jmp-exporter`

The `jmp-exporter` or `jmp exporter` CLI allows you to run Jumpstarter exporters
and management of exporter configs.
