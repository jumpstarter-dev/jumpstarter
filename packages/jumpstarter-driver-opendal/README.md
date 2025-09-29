# OpenDAL driver

`jumpstarter-driver-opendal` provides functionality for interacting with
storages attached to the exporter.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-opendal
```

## Configuration

Example configuration:

```{literalinclude} opendal.yaml
:language: yaml
```

### Configuration Parameters

- **`scheme`** (required): The storage service type (e.g., "fs", "s3", "gcs"). See [OpenDAL services](https://docs.rs/opendal/latest/opendal/services/index.html) for supported options.
- **`kwargs`** (required): Service-specific configuration parameters passed to the OpenDAL operator.
- **`remove_created_on_close`** (optional, default: `false`): When enabled, automatically removes all files and directories created during the session when the driver is closed.

### File/Directory Tracking and Cleanup

The OpenDAL driver tracks all files and directories created during a session:

- **File Creation**: Files opened in write modes (`"wb"`, `"w"`, `"ab"`, `"a"`)
- **Directory Creation**: Directories created via `create_dir()`
- **Copy Operations**: Target files/directories from `copy()` operations
- **Rename Operations**: Target files/directories from `rename()` operations (source is removed from tracking)

**Automatic Cleanup**: The tracking is automatically updated when resources are removed:
- **Delete Operations**: `delete()` removes the path from tracking
- **Remove Operations**: `remove_all()` removes the path from tracking

**Cleanup Behavior**: When `remove_created_on_close: true`, all tracked files and directories are automatically removed when the driver closes (filesystem only)

### Tracking API

```python
# Get all created resources (files and directories)
created_resources = await driver.get_created_resources()  # Returns set[str]

# Example usage
for path in created_resources:
    print(f"Created: {path}")
```

#### Use Cases

**Temporary File Management:**
```yaml
# Enable cleanup for temporary storage
remove_created_on_close: true
```

**Persistent Storage:**
```yaml
# Disable cleanup to preserve files (default)
remove_created_on_close: false
```

**Note:** Pre-existing files that are written to are treated as "created" since they may be remnants from failed cleanup operations.

## API Reference

### Examples

```{doctest}
>>> from tempfile import NamedTemporaryFile
>>> opendal.create_dir("test/directory/")
>>> opendal.write_bytes("test/directory/file", b"hello")
>>> assert opendal.hash("test/directory/file", "md5") == "5d41402abc4b2a76b9719d911017c592"
>>> opendal.remove_all("test/")
```

```{testsetup} *
from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
from jumpstarter.common.utils import serve

instance = serve(
    ExporterConfigV1Alpha1DriverInstance.from_path("source/reference/package-apis/drivers/opendal.yaml"
).instantiate())

opendal = instance.__enter__()
```

```{testcleanup} *
instance.__exit__(None, None, None)
```

### Client API

```{eval-rst}
.. autoclass:: jumpstarter_driver_opendal.client.OpendalClient()
    :members:

.. autoclass:: jumpstarter_driver_opendal.client.OpendalFile()
    :members:

.. autoclass:: jumpstarter_driver_opendal.common.Metadata()
    :members:
    :undoc-members:
    :exclude-members: model_config

.. autoclass:: jumpstarter_driver_opendal.common.EntryMode()
    :members:
    :undoc-members:
    :exclude-members: model_config

.. autoclass:: jumpstarter_driver_opendal.common.PresignedRequest()
    :members:
    :undoc-members:
    :exclude-members: model_config

.. autoclass:: jumpstarter_driver_opendal.common.Capability()
    :members:
    :undoc-members:
    :exclude-members: model_config
```
