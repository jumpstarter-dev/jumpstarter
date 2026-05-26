from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    # Subscribe to event group 1
    someip.subscribe_eventgroup(1)

    # Wait for event notifications
    try:
        event = someip.receive_event(timeout=10.0)
        print(f"Event service={event.service_id:#06x} id={event.event_id:#06x}")
        print(f"Payload: {bytes.fromhex(event.payload)}")
    finally:
        someip.unsubscribe_eventgroup(1)
