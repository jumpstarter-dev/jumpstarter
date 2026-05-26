from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    # Discover available services
    services = someip.find_service(0x1234, timeout=3.0)
    for svc in services:
        print(f"Found: service={svc.service_id:#06x} instance={svc.instance_id:#06x}")

    # Call the first discovered service
    if services:
        resp = someip.rpc_call(0x1234, 0x0001, b"\x10\x20")
        print(f"RPC result: {resp.payload}")
