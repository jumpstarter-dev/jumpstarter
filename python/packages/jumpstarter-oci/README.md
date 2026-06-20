# jumpstarter-oci

OCI registry authentication utilities shared by Jumpstarter drivers that pull
container images (flashers, qemu, ridesx).

Resolves registry credentials with three-level precedence — explicit arguments,
`OCI_USERNAME`/`OCI_PASSWORD` environment variables, then standard container
auth files (Podman `auth.json` / Docker `config.json`) — and parses registry
hostnames from OCI references, honouring `unqualified-search-registries` from
`registries.conf`.

```python
from jumpstarter_oci import resolve_oci_credentials

creds = resolve_oci_credentials("oci://quay.io/org/image:tag")
```
