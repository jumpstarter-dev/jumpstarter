def test_firmware_update(client):
    proxy = client.proxy

    with proxy.session(mode="mock", web_ui=True):
        with proxy.mock_endpoint(
            "GET", "/api/v1/updates/check",
            body={"update_available": True, "version": "2.6.0"},
        ):
            # DUT will see the mocked update
            trigger_update_check(client)
            assert_update_dialog_shown(client)
        # Mock auto-removed here
    # Proxy auto-stopped here
