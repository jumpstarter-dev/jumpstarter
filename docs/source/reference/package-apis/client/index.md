# Client API

This section provides reference documentation for Jumpstarter's client APIs. Covering topics such as:

- Listing exporters
- Requesting a lease
- Listing leases

please see the [Client API Reference](reference.md) for more details.

## Listing Exporters

The client library provides a function to list all exporters in the cluster.

```python
from jumpstarter.config.client import ClientConfigV1Alpha1

client = ClientConfigV1Alpha1.from_file("~/.config/jumpstarter/clients/majopela.yaml")
response = client.list_exporters(include_leases=True, include_online=True, page_size=1000)
for exporter in response.exporters:
    if exporter.online:
        print(exporter.name)
        print(exporter.lease)

print("next page token: ", response.next_page_token)
```

```{toctree}
:maxdepth: 1
:hidden:
reference.md
```
