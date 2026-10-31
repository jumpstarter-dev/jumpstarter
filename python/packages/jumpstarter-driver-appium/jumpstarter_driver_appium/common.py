import json
import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

# Uploaded applications are referenced by this prefix in capabilities and commands.
APP_TOKEN_PREFIX = "jumpstarter-app:"
APP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
APP_SUFFIXES = (".apk", ".aab", ".apks", ".ipa", ".zip")


def check_app_name(name: str) -> str:
    if not isinstance(name, str) or not APP_NAME.match(name) or not name.lower().endswith(APP_SUFFIXES):
        raise ValueError(f"Application file names must be plain names ending in {', '.join(APP_SUFFIXES)}")
    return name


@dataclass(frozen=True)
class AppiumTarget:
    """An Appium server for one leased target, valid inside its context.

    Open WebDriver sessions on ``url`` with ``capabilities()``. Where Appium
    runs is the driver's choice: on the exporter, reached over the lease, or on
    this machine for targets only it can reach.
    """

    url: str
    default_capabilities: Mapping[str, Any] = field(default_factory=dict)
    ca_file: Path | None = None
    wda_url: str | None = None

    def __post_init__(self):
        frozen = json.loads(json.dumps(dict(self.default_capabilities), allow_nan=False))
        object.__setattr__(self, "default_capabilities", MappingProxyType(frozen))

    def _check_active(self):
        if self.ca_file is not None and not self.ca_file.is_file():
            raise RuntimeError("The Appium target context has closed")

    def capabilities(self) -> dict[str, Any]:
        """W3C capabilities for a session on this target; add your own before use."""
        return json.loads(json.dumps(dict(self.default_capabilities)))

    def ssl_context(self) -> ssl.SSLContext | None:
        """Python's normal trust plus this target's CA, or None for plain HTTP."""
        self._check_active()
        if self.ca_file is None:
            return None
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(self.ca_file))
        return context

    def client_config(self, **kwargs):
        """An Appium-Python-Client ``AppiumClientConfig`` that trusts this target.

        Needs the ``Appium-Python-Client`` package, which Jumpstarter does not install.
        """
        self._check_active()
        from appium.webdriver.client_config import AppiumClientConfig  # ty: ignore[unresolved-import]

        if self.ca_file is not None:
            kwargs.setdefault("ca_certs", str(self.ca_file))
        return AppiumClientConfig(remote_server_addr=self.url, **kwargs)

    def _node_trust(self, configured: str | None) -> str:
        """Node reads one extra CA file, so extend an existing one without modifying it."""
        assert self.ca_file is not None
        if not configured:
            return str(self.ca_file)
        combined = self.ca_file.with_name("node-extra-ca.pem")
        existing = Path(configured).read_text(encoding="ascii")
        if "PRIVATE KEY" in existing:
            raise ValueError("NODE_EXTRA_CA_CERTS must contain public certificates only")
        combined.write_text(existing + "\n" + self.ca_file.read_text(encoding="ascii"), encoding="ascii")
        return str(combined)

    def environment(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """Copy the environment and describe this target to a tool in another language.

        Adds ``JUMPSTARTER_APPIUM_URL``, ``JUMPSTARTER_APPIUM_CAPABILITIES`` (JSON)
        and, for HTTPS targets, ``JUMPSTARTER_APPIUM_CA_FILE`` and Node's
        ``NODE_EXTRA_CA_CERTS``. Certificate verification stays enabled.
        """
        self._check_active()
        env = dict(os.environ if base is None else base)
        if env.get("NODE_TLS_REJECT_UNAUTHORIZED", "").strip() == "0":
            raise ValueError("NODE_TLS_REJECT_UNAUTHORIZED=0 disables HTTPS verification; remove it first")
        env["JUMPSTARTER_APPIUM_URL"] = self.url
        env["JUMPSTARTER_APPIUM_CAPABILITIES"] = json.dumps(self.capabilities())
        if self.wda_url is not None:
            env["JUMPSTARTER_APPIUM_WDA_URL"] = self.wda_url
        if self.ca_file is not None:
            env["JUMPSTARTER_APPIUM_CA_FILE"] = str(self.ca_file)
            env["NODE_EXTRA_CA_CERTS"] = self._node_trust(env.get("NODE_EXTRA_CA_CERTS"))
        return env
