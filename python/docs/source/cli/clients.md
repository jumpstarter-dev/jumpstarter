# Manage Clients

The Jumpstarter CLI can be used to manage your client configurations.

## Creating a client token and configuration

```bash
jumpstarter client create my-client -o my-client.yaml
```

This creates a client named `my-client` and outputs the configuration to a YAML
file called `my-client.yaml`:

```yaml
client:
    name: my-client
    endpoint: "grpcs://jumpstarter.my-lab.com:1443"
    token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
```
