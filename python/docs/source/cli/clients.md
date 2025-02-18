# Manage Clients

The `jmp admin` admin CLI can be used to manage your client configurations
on the distributed service.

## Creating a Client

If you have configured [a Jumpstarter service](../introduction/service.md)
and you have a kubeconfig, the [`jmp admin` CLI](./reference/jmp-admin.md#jmp-admin-create-client) will attempt to use
your current credentials to provision the client automatically, and produce
a client configuration file.

You can also use the following options to specify kubeconfig and context to use:

- `--kubeconfig` - Set the location of your kubeconfig file.
- `--namespace` - The namespace to search in (default is `default`)

To create a new client and its associated config, run the following command:

```bash
$ jmp admin create client john --namespace jumpstarter-lab --unsafe -o john.yaml
```

This creates a client named `john` and outputs the configuration to a YAML
file named `john.yaml`:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  namespace: jumpstarter-lab
  name: john
endpoint: grpc.jumpstarter.192.168.1.10.nip.io:8082
token: <<token>>
tls:
  ca: ''
  insecure: False
drivers:
  allow: []
  unsafe: True
```

We use the `--unsafe` setting that configures the `drivers` section to allow
any driver packages on the client.

```{warning}
This section can be important if you don't trust the exporter's configuration, since every
driver is composed of two parts, a client side and an exporter side, the client side Python module
is dynamically loaded when a client connects to an exporter.
```

A `tls` section is also included, which allows you to specify a custom CA certificate
to use for the connection, or to disable TLS verification if your system is using
self-signed certificates.

### Manual Provisioning

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

    Then the user can create the client using the [jmp client](./reference/jmp-client.md#jmp-client-create-config) CLI:

    ```bash
    $ jmp client create-config my-client
    Enter a valid Jumpstarter service endpoint: devl.jumpstarter.dev
    Enter a Jumpstarter auth token (hidden): ***
    Enter a comma-separated list of allowed driver packages (optional):
    ```
