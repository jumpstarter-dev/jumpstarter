"""
Jumpstarter corellium driver(s) implementation module.
"""

import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Dict, Optional

from jumpstarter_driver_network.driver import WebsocketNetwork
from jumpstarter_driver_power.driver import PowerReading, VirtualPowerInterface

from .corellium.api import ApiClient
from .corellium.types import Instance
from jumpstarter.common import exceptions as jmp_exceptions
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class Corellium(Driver):
    """
    Corellium top-level driver.
    """

    _api: ApiClient = field(init=False)
    project_id: str
    device_name: str
    device_flavor: str
    device_os: str = field(default="1.1.1")
    device_build: str = field(default="Critical Application Monitor (Baremetal)")
    console_name: str = field(default="Primary Compute Non-Secure")

    @classmethod
    def client(cls) -> str:
        """
        Return the driver's client.
        """
        return "jumpstarter_driver_corellium.client.CorelliumClient"

    def __post_init__(self) -> None:
        """
        Post initialization method.

        It will check for the following environment variables:

        - CORELLIUM_API_HOST
        - CORELLIUM_API_TOKEN

        Raises an exception in case these variables are not set or empty.

        Additionally, it also sets up some internal objects/varibales such as:

        - Corellium API client instance
        - Children jumpstarter drives
        """
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        api_host = self.get_env_var("CORELLIUM_API_HOST")
        api_token = self.get_env_var("CORELLIUM_API_TOKEN")
        self._api = ApiClient(api_host, api_token)

        self.children["power"] = CorelliumPower(parent=self)
        self.children["serial"] = CorelliumConsole(parent=self, url="")

    def get_env_var(self, name: str) -> str:
        """
        Return an env var and raise an exception if said
        var does not exist or is empty.
        """
        value = os.environ.get(name)

        if value is None:
            raise jmp_exceptions.ConfigurationError(f'Missing "{name}" environment variable')

        value = value.strip()

        if len(value) == 0:
            raise jmp_exceptions.ConfigurationError(f'"{name}" environment variable is empty')

        return value

    @property
    def api(self) -> ApiClient:
        """
        Return the Corellium API client object.
        """
        return self._api


@dataclass(kw_only=True)
class CorelliumPower(VirtualPowerInterface, Driver):
    """
    Power driver implementation for corellium virtual devices.

    This driver will create and destroy virtual instances.
    """

    parent: Corellium

    def get_timeout_opts(self) -> Dict[str, int]:
        """
        Return config/opts to be used when waiting for Corellium's API.
        """
        return {
            "retries": int(os.environ.get("CORELLIUM_API_RETRIES", 12)),
            "interval": os.environ.get("CORELLIUM_API_INTERVAL", 5),
        }

    def wait_instance(self, current: Instance, desired: Optional[Instance]):
        """
        Wait for `current` instance to reach the same state as the `desired` instance.

        Desired can also be set to None, which means the instance should not exist.
        """
        opts = self.get_timeout_opts()
        counter = 0

        while True:
            if counter >= opts["retries"]:
                raise ValueError(f"Instance took too long to be reach the desired state: {current}")

            if self.parent.api.get_instance(current.id) == desired:
                break

            counter += 1
            time.sleep(opts["interval"])

    @export
    def on(self) -> None:
        """
        Power a Corellium virtual device on.

        It will create an instance if one does not exist, it will just power the existing one on otherwise.
        """
        self.logger.info("Corellium Device:")
        self.logger.info(f"\tDevice Name: {self.parent.device_name}")
        self.logger.info(f"\tDevice Flavor: {self.parent.device_flavor}")
        self.logger.info(f"\tDevice OS Version: {self.parent.device_os}")

        project = self.parent.api.get_project(self.parent.project_id)
        if project is None:
            raise ValueError(f"Unable to fetch project: {self.parent.project_id}")
        self.logger.info(f"Using project: {project.name}")

        device = self.parent.api.get_device(self.parent.device_flavor)
        if device is None:
            raise ValueError("Unable to find a device for this model: {self.parent.device_model}")
        self.logger.info(f"Using device spec: {device.name}")

        # retrieve an existing instance first
        instance = self.parent.api.get_instance(self.parent.device_name)
        if instance:
            self.parent.api.set_instance_state(instance, "on")
        # create a new one otherwise
        else:
            opts = {}
            if self.parent.device_os:
                opts["os_version"] = self.parent.device_os
            if self.parent.device_build:
                opts["os_build"] = self.parent.device_build
            instance = self.parent.api.create_instance(self.parent.device_name, project, device, **opts)
        self.logger.info(f"Instance: {self.parent.device_name} (ID: {instance.id})")

        self.wait_instance(instance, Instance(id=instance.id, state="on"))

    @export
    def off(self, destroy: bool = False) -> None:
        """
        Destroy a Corellium virtual device/instance.
        """
        # fail if project does not exist
        project = self.parent.api.get_project(self.parent.project_id)
        if project is None:
            raise ValueError(f"Unable to fetch project: {self.parent.project_id}")

        # get instance and fail if instance does not exist
        instance = self.parent.api.get_instance(self.parent.device_name)
        if instance is None:
            raise ValueError("Instance does not exist")

        self.parent.api.set_instance_state(instance, "off")
        self.wait_instance(instance, Instance(id=instance.id, state="off"))

        if destroy:
            self.parent.api.destroy_instance(instance)
            self.wait_instance(instance, None)

    @export
    def read(self) -> AsyncGenerator[PowerReading, None]:
        raise NotImplementedError


@dataclass(kw_only=True)
class CorelliumConsole(WebsocketNetwork):
    """
    A serial console driver that uses a network websocket to connect
    to the remote virtual instance console.
    """

    parent: Corellium
    baudrate: int = field(default=115200)

    @classmethod
    def client(cls) -> str:
        """
        Use pyserial client to re-use its console implementation.
        """
        return "jumpstarter_driver_pyserial.client.PySerialClient"

    @property
    def url(self) -> str:
        """
        Retrieve console url from Corellium's API to be used by
        other drivers, such as the PySerial one.

        Overwrites the parent's url property so the required
        API calls are only invoked when the property is used.
        """
        project = self.parent.api.get_project(self.parent.project_id)
        if project is None:
            raise ValueError(f"Unable to fetch project: {self.parent.project_id}")

        # get instance and fail if instance does not exist
        instance = self.parent.api.get_instance(self.parent.device_name)
        if instance is None:
            raise ValueError("Instance does not exist or is powered off")

        console_id = self.parent.api.get_instance_console_id(instance, self.parent.console_name)
        if console_id is None:
            raise ValueError('Console ID not found for "{self.patent.console_name}"')
        console_url = self.parent.api.get_instance_console_url(instance, console_id)

        return console_url

    @url.setter
    def url(self, value: str) -> None:
        pass
