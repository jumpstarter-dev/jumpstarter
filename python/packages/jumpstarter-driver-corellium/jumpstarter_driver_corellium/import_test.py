import pytest

pytest.importorskip("jumpstarter_driver_corellium")


def test_import_corellium_module() -> None:
    import jumpstarter_driver_corellium  # noqa: F811

    assert jumpstarter_driver_corellium is not None
