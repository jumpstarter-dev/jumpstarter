# Manage Clients

The Jumpstarter CLI can be used to manage your client configurations.

## Creating a Client Config

To create a new client config, run the following command:

```bash
$ jumpstarter client create my-client
```

### Automatic Provisioning

If you have [Kubectl](https://www.downloadkubernetes.com/) installed on your
system and the current context contains an installation of the 
[Jumpstarter service](../introduction/service.md), the CLI will attempt to use
your admin credentials to provision the client automatically.

You can also use the following options to specify kubeconfig and context to use:

- `--kubeconfig` - Set the location of your kubeconfig file.
- `--context` - The context to use (default is the `current-context`).
- `--namespace` - The namespace to search in (default is `jumpstarter-lab`)

This creates a client a new client named `my-client` and outputs the configuration to a YAML
file called `my-client.yaml`:

```yaml
client:
    name: my-client
    endpoint: "jumpstarter.my-lab.com:1443"
    token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
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

    % TODO: Determine the actual instructions here.
    ```bash
    $ kubectl get client my-client
    ...
    ```

3. Create the client config manually:

    ```bash
    $ jmp client create --manual
    Enter a valid Jumpstarter service endpoint: devl.jumpstarter.dev
    Enter a Jumpstarter auth token (hidden): ***
    Enter a comma-separated list of allowed driver packages (optional):
    ```
