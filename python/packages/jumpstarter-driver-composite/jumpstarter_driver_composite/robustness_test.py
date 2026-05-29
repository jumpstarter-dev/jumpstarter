import pytest

pytest.importorskip("jumpstarter_driver_composite")


def test_import_composite_module() -> None:
    import jumpstarter_driver_composite  # noqa: F811

    assert jumpstarter_driver_composite is not None
