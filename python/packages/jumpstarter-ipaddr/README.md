# jumpstarter-ipaddr

Helpers for discovering the host's reachable IP address, shared by Jumpstarter
drivers that serve content back to a DUT (http, tftp).

`get_ip_address()` resolves the host's non-loopback address (falling back to a
UDP-connect probe when the hostname resolves to loopback); `get_minikube_ip()`
queries a minikube profile's node IP.

```python
from jumpstarter_ipaddr import get_ip_address

addr = get_ip_address()
```
