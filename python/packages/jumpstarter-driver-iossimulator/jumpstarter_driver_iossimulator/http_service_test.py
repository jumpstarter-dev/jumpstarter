from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from . import http_service as module
from .http_service import HttpServiceProvider, SimulatorHttpContext, create_http_service
from jumpstarter.common.exceptions import ConfigurationError


def test_context_identity_and_metadata(tmp_path):
    context = SimulatorHttpContext(tmp_path, tmp_path / "devices", "owned", "27.0")
    with pytest.raises(FrozenInstanceError):
        context.udid = "other"  # ty: ignore[invalid-assignment]
    assert context.device_metadata() == {"platform": "iOS", "udid": "owned", "version": "27.0"}


def test_provider_from_exporter_config(tmp_path, monkeypatch):
    captured = []

    class Provider(HttpServiceProvider):
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def metadata(self):
            return {}

        def connect(self):
            raise NotImplementedError

        def close(self):
            pass

    importer = Mock(return_value=Provider)
    monkeypatch.setattr(module, "import_class", importer)
    context = SimulatorHttpContext(tmp_path, tmp_path / "devices", "owned", "27.0")
    value = create_http_service({"provider": "operator.Provider", "options": {"setting": True}}, context)
    assert isinstance(value, Provider)
    assert captured == [{"context": context, "setting": True}]
    importer.assert_called_once_with("operator.Provider", allow=[], unsafe=True)


@pytest.mark.parametrize("provider", [object, object(), lambda: None])
def test_invalid_provider_contract_rejected(provider, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "import_class", lambda *args, **kwargs: provider)
    context = SimulatorHttpContext(tmp_path, tmp_path / "devices", "owned", "27.0")
    with pytest.raises(ConfigurationError, match="implement"):
        create_http_service({"provider": "operator.Provider"}, context)
