from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    someip.rpc_call(0x1234, 0x0001, b"\x01")

    someip.reconnect()

    someip.rpc_call(0x1234, 0x0001, b"\x02")

    someip.close_connection()
