from dataclasses import dataclass, field
from typing import Generator, Optional

import requests
from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.driver import PowerInterface

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class HttpEndpointConfig:
    url: str = field()
    method: str = field(default='GET')
    data: Optional[str] = field(default=None)


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
        kwargs = {
            'url': endpoint_config.url,
            'auth': auth,
        }
        if endpoint_config.data and method in ['POST', 'PUT', 'PATCH']:
            kwargs['data'] = endpoint_config.data

        self.logger.debug(f"Making {method} request to {endpoint_config.url}")

        response = requests.request(method, **kwargs)
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
        """Read power measurements via HTTP request

        Note: Response parsing for voltage/current is not implemented yet.
        Returns dummy values for now.
        """
        self.logger.info("Reading power measurements via HTTP")
        if self.power_read is None:
            self.logger.error("Power read endpoint not configured")
            yield PowerReading(voltage=0.0, current=0.0)
            return

        self._make_http_request(self.power_read)

        # TODO: Parse response_text to extract voltage and current values
        # For now, return dummy values
        self.logger.warning("Power reading response parsing not implemented, returning dummy values")
        yield PowerReading(voltage=0.0, current=0.0)
