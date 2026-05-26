from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    someip.send_message(0x1234, 0x0001, b"\xAA\xBB")
    msg = someip.receive_message(timeout=2.0)
    print(f"Received from service={msg.service_id:#06x}: {msg.payload}")
