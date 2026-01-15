# Client API Reference

## Loading config files
```{eval-rst}
.. autoclass:: jumpstarter.config.client.ClientConfigV1Alpha1()
    :members: from_file, from_env, load, list
    :exclude-members: __init__
```

## Manipulating config files
```{eval-rst}
.. autoclass:: jumpstarter.config.client.ClientConfigV1Alpha1()
    :no-index:
    :members: save, dump_yaml, list, delete
    :exclude-members: __init__
```

## Interact with the service
```{eval-rst}
.. autoclass:: jumpstarter.config.client.ClientConfigV1Alpha1()
    :no-index:
    :members: get_exporter, list_exporters, create_lease, delete_lease, lease, list_leases, update_lease
    :exclude-members: __init__
```
