from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    # Perform operations...
    someip.rpc_call(0x1234, 0x0001, b"\x01")

    # Reconnect after network disruption
    someip.reconnect()

    # Continue operations
    someip.rpc_call(0x1234, 0x0001, b"\x02")

    # Clean up
    someip.close_connection()
