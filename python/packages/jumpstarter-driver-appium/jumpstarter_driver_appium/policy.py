"""Keep lease clients from steering the exporter's Appium at exporter files or other hosts.

Appium is built as a trusted local tool. Even with every insecure feature
denied, a few capabilities and command arguments make it read exporter paths
or fetch and upload URLs. The policy rejects those, rewrites uploaded
application tokens to their exporter paths and pins the capabilities the
driver fixes. Everything else reaches Appium unchanged.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from .common import APP_TOKEN_PREFIX

# Capabilities whose string values name exporter files, directories, executables or URLs.
_HOST_CAPABILITY = re.compile(r"(?:Path|Paths|Dir|Directory|Executable|Url|URL|Urls|File|Files)$")
# Arguments that take an application to install; only uploaded applications are accepted.
_APP_KEYS = frozenset({"app", "otherApps", "appPath"})
# Arguments that make Appium upload results to a URL of the caller's choice.
_DENIED_KEYS = frozenset({"remotePath"})
# W3C capabilities that route or reconfigure the exporter's own network access.
_DENIED_STANDARD = frozenset({"proxy"})
_MAX_DEPTH = 64


class PolicyError(ValueError):
    """A request is outside what a lease may ask of the exporter's Appium."""


def _name(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def _has_text(value: Any) -> bool:
    if isinstance(value, str):
        return True
    if isinstance(value, list):
        return any(_has_text(item) for item in value)
    if isinstance(value, dict):
        return any(_has_text(item) for item in value.values())
    return False


class AppiumPolicy:
    """Check one request body at a time for one lease.

    ``fixed`` capabilities are set on every new session and replace any value
    the client sends. ``session`` returns capabilities chosen afresh for each
    new session, such as reserved ports. ``resolve`` maps an uploaded
    application token to its exporter path, raising ``PolicyError`` for unknown tokens.
    """

    def __init__(
        self,
        fixed: dict[str, Any],
        resolve: Callable[[str], str],
        session: Callable[[], dict[str, Any]] | None = None,
    ):
        self.fixed = dict(fixed)
        self.resolve = resolve
        self.session = session

    def request(self, method: str, path: str, body: bytes) -> bytes:
        """Return the body to forward, or raise ``PolicyError``."""
        if method != "POST" or not body:
            return body
        try:
            document = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PolicyError("Request bodies must be JSON") from exc
        if not isinstance(document, dict):
            raise PolicyError("Request bodies must be JSON objects")
        if path.split("?", 1)[0].rstrip("/") == "/session":
            document = self.new_session(document)
        else:
            document = self._arguments(document, 0)
        return json.dumps(document, allow_nan=False).encode()

    def new_session(self, body: dict[str, Any]) -> dict[str, Any]:
        capabilities = body.get("capabilities", {})
        if not isinstance(capabilities, dict):
            raise PolicyError("capabilities must be an object")
        always = capabilities.get("alwaysMatch", {})
        first = capabilities.get("firstMatch", [{}])
        if not isinstance(always, dict) or not isinstance(first, list) or not first:
            raise PolicyError("capabilities must have an alwaysMatch object and a nonempty firstMatch list")
        if not all(isinstance(choice, dict) for choice in first):
            raise PolicyError("firstMatch entries must be objects")
        always = self._capabilities(always)
        if self.session is not None:
            self.fixed.update(self.session())
        always.update(self.fixed)
        first = [
            {key: value for key, value in self._capabilities(choice).items() if key not in self.fixed}
            for choice in first
        ]
        # Legacy desiredCapabilities would bypass the W3C merge above.
        return {"capabilities": {"alwaysMatch": always, "firstMatch": first}}

    def _capabilities(self, capabilities: dict[str, Any]) -> dict[str, Any]:
        flat = {key: value for key, value in capabilities.items() if key != "appium:options"}
        options = capabilities.get("appium:options", {})
        if not isinstance(options, dict):
            raise PolicyError("appium:options must be an object")
        for key, value in options.items():
            if f"appium:{key}" in flat:
                raise PolicyError(f"appium:{key} is set both directly and in appium:options")
            flat[f"appium:{key}"] = value
        checked = {}
        for key, value in flat.items():
            if key in self.fixed:
                continue
            name = _name(key)
            if key in _DENIED_STANDARD:
                raise PolicyError(f"The {key} capability is not available on Jumpstarter targets")
            if name in _APP_KEYS:
                value = self._apps(name, value)
            elif _HOST_CAPABILITY.search(name) and _has_text(value):
                raise PolicyError(f"{key} would point the exporter's Appium at its files or other hosts")
            else:
                value = self._arguments(value, 0)
            checked[key] = value
        return checked

    def _apps(self, name: str, value: Any) -> Any:
        hint = "upload the application with upload_app() and pass the token it returns"
        if name == "otherApps" and isinstance(value, str) and value.lstrip().startswith("["):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise PolicyError(f"otherApps must list uploaded applications; {hint}") from exc
        if isinstance(value, list):
            return [self._apps(name, item) for item in value]
        if not isinstance(value, str) or not value.startswith(APP_TOKEN_PREFIX):
            raise PolicyError(f"{name} accepts only uploaded applications; {hint}")
        return self.resolve(value)

    def _arguments(self, value: Any, depth: int) -> Any:
        if depth > _MAX_DEPTH:
            raise PolicyError("Request body is nested too deeply")
        if isinstance(value, dict):
            checked = {}
            for key, item in value.items():
                if key in _DENIED_KEYS:
                    raise PolicyError(f"{key} would make the exporter upload to another host; omit it")
                checked[key] = self._apps(key, item) if key in _APP_KEYS else self._arguments(item, depth + 1)
            return checked
        if isinstance(value, list):
            return [self._arguments(item, depth + 1) for item in value]
        if isinstance(value, str) and value.startswith(APP_TOKEN_PREFIX):
            return self.resolve(value)
        return value
