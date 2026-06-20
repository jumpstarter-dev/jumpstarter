# jumpstarter-storage

Async helpers for streaming images to and from raw block-storage devices, shared
by Jumpstarter drivers that expose a DUT's storage (dutlink, sdwire).

Waits for a storage device to appear and settle (handling `ENOMEDIUM`/`EIO`
while a device mux switches), then pumps an anyio byte stream to/from it with
progress logging and a bounded `fsync`.

```python
from jumpstarter_storage import write_to_storage_device

await write_to_storage_device("/dev/sda", resource, logger=logger)
```
