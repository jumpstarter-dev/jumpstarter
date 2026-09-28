import os
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
@pytest.mark.parametrize("force_system_certs", [None, "0", "1"])
def test_system_certificates_without_uname(monkeypatch, platform, force_system_certs):
    monkeypatch.delattr(os, "uname", raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    if force_system_certs is None:
        monkeypatch.delenv("JUMPSTARTER_FORCE_SYSTEM_CERTS", raising=False)
    else:
        monkeypatch.setenv("JUMPSTARTER_FORCE_SYSTEM_CERTS", force_system_certs)

    with patch("truststore.inject_into_ssl") as inject:
        runpy.run_path(str(Path(__file__).with_name("__init__.py")))

    if platform != "darwin" or force_system_certs == "1":
        inject.assert_called_once_with()
    else:
        inject.assert_not_called()
