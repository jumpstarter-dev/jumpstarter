# Manage Clients

The `jmp admin` CLI can be used to manage your client configurations on
the distributed service.

## Creating a Client

If you have configured [a Service](../introduction/service.md) and
you have a kubeconfig, the [`jmp admin`
CLI](reference/jmp-admin.md#jmp-admin-cli-reference) will attempt to use your
current credentials to provision the client automatically and produce a client
configuration file.

You can also use the following options to specify the kubeconfig and context to use:

- `--kubeconfig` - Set the location of your kubeconfig file.
- `--namespace` - The namespace to search in (default is `default`)

To create a new client and its associated config, run the following command:

```shell
$ jmp admin create client john --namespace jumpstarter-lab --unsafe -o john.yaml
```

This creates a client named `john` and outputs the configuration to a YAML file
named `john.yaml`:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  namespace: jumpstarter-lab
  name: john
endpoint: grpc.jumpstarter.192.168.1.10.nip.io:8082
token: <<token>>
grpcConfig:
  # please refer to the https://grpc.github.io/grpc/core/group__grpc__arg__keys.html documentation
  grpc.keepalive_time_ms: 20000
tls:
  ca: ''
  insecure: False
drivers:
  allow: []
  unsafe: True
```

We use the `--unsafe` setting that configures the `drivers` section to allow any
driver packages on the client.

```{warning}
The drivers configuration is an important security consideration. When a client connects to an exporter, 
the client-side Python modules for drivers are dynamically loaded. If you don't fully trust the exporter's 
configuration, you should carefully restrict which driver packages are allowed to load on the client.
```

A `tls` section is also included, which allows you to specify a custom CA
certificate to use for the connection, or to disable TLS verification if your
system is using self-signed certificates.

### Manual Provisioning

1. Apply the YAML to your cluster:

    ```yaml
    # my-client.yaml
    apiVersion: jumpstarter.dev/v1alpha1
    kind: Client
    metadata:
      name: my-client
    ```

    ```shell
    $ kubectl apply -f my-client.yaml
    ```

2. Retrieve the created client resource information:

    ```shell
    $ kubectl get client my-client -o yaml
    $ kubectl get client my-client -o=jsonpath='{.status.endpoint}'
    $ kubectl get secret $(kubectl get client my-client -o=jsonpath='{.status.credential.name}') -o=jsonpath='{.data.token}' | base64 -d
    ```

3. Store these credentials securely as a CI secret or distribute them to the appropriate end user.

    The end user can then configure their client using the
    [jmp](./reference/jmp.md#jmp-cli-reference) CLI:

    ```shell
    $ jmp config client create my-client
    Enter a valid Service endpoint: devl.jumpstarter.dev
    Enter a Jumpstarter auth token (hidden): ***
    Enter a comma-separated list of allowed driver packages (optional):
    ```
