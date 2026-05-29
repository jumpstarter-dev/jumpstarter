import pytest

pytest.importorskip("jumpstarter_driver_network")


def test_import_network_module() -> None:
    import jumpstarter_driver_network  # noqa: F811

    assert jumpstarter_driver_network is not None
