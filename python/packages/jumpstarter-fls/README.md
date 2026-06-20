# jumpstarter-fls

Utilities for locating the [FLS](https://github.com/jumpstarter-dev/fls) flasher
binary used by Jumpstarter drivers that flash devices via fastboot with OCI
image support (flashers, qemu, ridesx).

FLS is normally pre-installed on the exporter `PATH`; these helpers add optional
configuration-driven overrides (a pinned GitHub-release version, or — when
explicitly allowed — a custom download URL).

```python
from jumpstarter_fls import get_fls_binary

path = get_fls_binary(fls_version="0.1.9")
```
