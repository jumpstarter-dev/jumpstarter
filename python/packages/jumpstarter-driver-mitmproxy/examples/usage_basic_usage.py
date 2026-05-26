def test_device_status(client):
    proxy = client.proxy

    # Start with web UI for debugging
    proxy.start(mode="mock", web_ui=True)

    # Mock a backend endpoint
    proxy.set_mock(
        "GET", "/api/v1/status",
        body={"id": "device-001", "status": "active"},
    )

    # ... interact with DUT ...

    proxy.stop()
