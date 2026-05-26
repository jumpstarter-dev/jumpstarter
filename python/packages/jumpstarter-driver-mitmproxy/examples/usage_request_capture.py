def test_telemetry_sent(client):
    proxy = client.proxy

    with proxy.capture() as cap:
        # ... DUT sends telemetry through the proxy ...
        cap.wait_for_request("POST", "/api/v1/telemetry", timeout=10)

    # After the block, cap.requests is a frozen snapshot
    assert len(cap.requests) >= 1
    cap.assert_request_made("POST", "/api/v1/telemetry")
