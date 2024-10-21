# Manage Clients

The `jmpctl` admin CLI can be used to manage your client configurations
on the distributed service.

## Creating a Client

If you have configured the [Jumpstarter service](../introduction/service.md),
and you have a kubeconfig the `jmpctl` CLI will attempt to use
your current credentials to provision the client automatically, and produce
a client configuration file.

You can also use the following options to specify kubeconfig and context to use:

- `--kubeconfig` - Set the location of your kubeconfig file.
- `--namespace` - The namespace to search in (default is `default`)

To create a new client and its associated config, run the following command:

```bash
$ jmpctl client create john --namespace jumpstarter-lab > john.yaml
$ cat >> john.yaml <<EOF
drivers:
  allow: []
  unsafe: True
EOF
```

This creates a client a new client named `john` and outputs the configuration to a YAML
file called `john.yaml`:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
endpoint: grpc.jumpstarter.192.168.1.10.nip.io:8082
token: <<token>>
drivers:
  allow: []
  unsafe: True
```

In addition we have included a `drivers` section in the configuration file, which
allows you to specify a list of allowed driver packages and enable unsafe mode (allow any driver).

```{warning}
This section can be important if you don't trust the exporter's configuration, since every
driver is composed of two parts, a cliend and a exporter side, the client side Python module
is dynamically loaded when a client connects to a exporter.
```

### Manual Provisioning

If you do not have Kubectl installed or don't have direct access to the cluster,
a client can also be provisioned manually on a different machine.

1. Apply the YAML to your cluster:

    ```yaml
    # my-client.yaml
    apiVersion: jumpstarter.dev/v1alpha1
    kind: Client
    metadata:
      name: my-client
    ```

    ```bash
    $ kubectl apply -f my-client.yaml
    ```

2. Get the created client resource:

    ```bash
    $ kubectl get client my-client -o yaml
    $ kubectl get client my-client -o=jsonpath='{.status.endpoint}'
    $ kubectl get secret $(kubectl get client my-client -o=jsonpath='{.status.credential.name}') -o=jsonpath='{.data.token}' | base64 -d
    ```

3. Those details can be installed as a secret on CI, or passed down to the final user.

    Then the user can create the client performing:

    ```bash
    $ jmp client create my-client
    Enter a valid Jumpstarter service endpoint: devl.jumpstarter.dev
    Enter a Jumpstarter auth token (hidden): ***
    Enter a comma-separated list of allowed driver packages (optional):
    ```
