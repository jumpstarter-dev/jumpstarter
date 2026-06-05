def test_driver_example_serve_creates_client(driver_example_module):
    assert hasattr(driver_example_module, "GenericDriver")
    assert hasattr(driver_example_module, "GenericClient")

    from jumpstarter.common.utils import serve

    with serve(driver_example_module.GenericDriver()) as client:
        result = client.query("e2e")
        assert result == "Response for e2e"

        data = list(client.get_data())
        assert len(data) == 3
        assert data[0]["type"] == "data"
