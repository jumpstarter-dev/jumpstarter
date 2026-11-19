from jumpstarter_driver_power.client import PowerClient


class NoyitoPowerClient(PowerClient):
    """Client for the NOYITO relay drivers.

    Adds nothing to ``PowerClient``, which has ``status`` built in. It is kept, and the
    drivers still name it, so clients on older releases keep loading their own
    ``NoyitoPowerClient`` (which had ``status`` before ``PowerClient`` did). It can go
    once those clients are no longer in use.
    """
