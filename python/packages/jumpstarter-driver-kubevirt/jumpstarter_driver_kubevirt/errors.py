"""Errors shared by the driver and its client."""

# recover() raises this when the VM carries a different, live operation: the
# caller's was preempted or never written, and its cleanup belongs to whoever
# wrote the one that is live now. The client recognizes it and stays quiet.
SUPERSEDED = "A different flash operation is active"
