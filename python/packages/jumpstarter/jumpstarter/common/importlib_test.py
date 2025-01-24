import pytest

from .importlib import import_class


def test_import_class():
    import_class("os.open", [], True)

    with pytest.raises(ImportError):
        import_class("os.invalid", [], True)

    with pytest.raises(ImportError):
        import_class("os.open", [], False)

    import_class("os.open", ["os.*"], False)

    with pytest.raises(ImportError):
        import_class("os.open", ["sys.*"], False)

    with pytest.raises(ImportError):
        import_class("os", [], True)
