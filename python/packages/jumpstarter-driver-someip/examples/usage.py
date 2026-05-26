from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    response = someip.rpc_call(0x1234, 0x0001, b"\x01\x02\x03")
    print(f"Response: {bytes.fromhex(response.payload)}")
    print(f"Return code: {response.return_code}")
