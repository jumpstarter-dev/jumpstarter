import importlib.util


def test_driver_example_imports_successfully(examples_root):
    driver_example = examples_root / "introduction" / "driver_example.py"
    spec = importlib.util.spec_from_file_location(
        "driver_example", str(driver_example)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)


def test_driver_example_serve_creates_client(examples_root):
    driver_example = examples_root / "introduction" / "driver_example.py"
    spec = importlib.util.spec_from_file_location(
        "driver_example", str(driver_example)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "GenericDriver")
    assert hasattr(mod, "GenericClient")

    from jumpstarter.common.utils import serve

    with serve(mod.GenericDriver()) as client:
        result = client.query("e2e")
        assert result == "Response for e2e"

        data = list(client.get_data())
        assert len(data) == 3
        assert data[0]["type"] == "data"
