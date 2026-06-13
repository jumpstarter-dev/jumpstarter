import json
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

import requests
from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.driver import PowerInterface

from jumpstarter.driver import Driver, export


def _json_path(data: Any, path: str) -> Any:
    """Walk a dotted path through nested dicts/lists, e.g. 'meters.0.power'."""
    cur = data
    for part in path.split("."):
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


@dataclass(kw_only=True)
class HttpEndpointConfig:
    url: str = field()
    method: str = field(default='GET')
    data: Optional[str] = field(default=None)
    # For read endpoints: dotted JSON paths to the values (e.g. "emeter.voltage").
    # When unset, read() looks for top-level "voltage"/"current" keys.
    voltage_path: Optional[str] = field(default=None)
    current_path: Optional[str] = field(default=None)


@dataclass(kw_only=True)
class HttpBasicAuth:
    user: str = field(default="")
    password: str = field(default="")


@dataclass(kw_only=True)
class HttpAuthConfig:
    basic: Optional[HttpBasicAuth] = field(default=None)


@dataclass(kw_only=True)
class HttpPower(PowerInterface, Driver):
    """HTTP Power driver for Jumpstarter

    Makes HTTP requests to control power and read power measurements.
    """
    name: str = field(default="device")
    # HTTP endpoints configuration
    power_on: HttpEndpointConfig = field()
    power_off: HttpEndpointConfig = field()
    power_read: Optional[HttpEndpointConfig] = field(default=None)
    # Authentication configuration
    auth: Optional[HttpAuthConfig] = field(default=None)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        # The structures don't get deserialized automatically for some reason.
        if isinstance(self.power_on, dict):
            self.power_on = HttpEndpointConfig(**self.power_on)
        if isinstance(self.power_off, dict):
            self.power_off = HttpEndpointConfig(**self.power_off)
        if self.power_read and isinstance(self.power_read, dict):
            self.power_read = HttpEndpointConfig(**self.power_read)
        if self.auth and isinstance(self.auth, dict):
            self.auth = HttpAuthConfig(**self.auth)
        if self.auth and self.auth.basic and isinstance(self.auth.basic, dict):
            self.auth.basic = HttpBasicAuth(**self.auth.basic)


    def _make_http_request(self, endpoint_config: HttpEndpointConfig) -> str:
        """Make HTTP request to the specified endpoint"""
        auth = None
        if self.auth and self.auth.basic:
            auth = (self.auth.basic.user, self.auth.basic.password)
        method = endpoint_config.method.upper()
        url = endpoint_config.url
        kwargs = {
            'auth': auth,
        }
        if endpoint_config.data and method in ['POST', 'PUT', 'PATCH']:
            kwargs['data'] = endpoint_config.data

        self.logger.debug(f"Making {method} request to {url}")

        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response.text

    @export
    def on(self):
        """Power on via HTTP request"""
        self.logger.info(f"Powering on {self.name} via HTTP")
        self._make_http_request(self.power_on)
        self.logger.debug("Powering on via HTTP DONE")

    @export
    def off(self):
        """Power off via HTTP request"""
        self.logger.info(f"Powering off {self.name} via HTTP")
        self._make_http_request(self.power_off)
        self.logger.debug("Powering off via HTTP DONE")

    @export
    def read(self) -> Generator[PowerReading, None, None]:
        """Read a power measurement from the configured read endpoint.

        Parses the JSON response and pulls voltage/current from the paths set on
        ``power_read`` (defaulting to top-level ``voltage``/``current`` keys).

        Requires ``power_read`` to be configured; raises ``ValueError`` if it is
        not, rather than reporting a fake zero measurement.
        """
        if self.power_read is None:
            raise ValueError("power_read endpoint is not configured")

        self.logger.debug("Reading power measurements via HTTP")
        text = self._make_http_request(self.power_read)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"read endpoint did not return JSON: {e}") from e

        voltage = self._extract_reading(data, self.power_read.voltage_path, "voltage")
        current = self._extract_reading(data, self.power_read.current_path, "current")
        yield PowerReading(voltage=voltage, current=current)

    @staticmethod
    def _extract_reading(data: Any, path: Optional[str], default_key: str) -> float:
        """Pull one numeric reading. A configured path that's missing is an error;
        a missing default key just means the device doesn't report it (0.0)."""
        key = path or default_key
        try:
            value = _json_path(data, key)
        except (KeyError, IndexError, TypeError, ValueError):
            if path is not None:
                raise ValueError(f"configured path {key!r} not found in read response") from None
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"value at {key!r} is not numeric (got {type(value).__name__})") from None
