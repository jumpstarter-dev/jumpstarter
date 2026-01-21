
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import requests
import requests.exceptions
from jumpstarter_driver_power.driver import PowerInterface, PowerReading

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class EnerGenie(PowerInterface, Driver):
    """
    driver for the EnerGenie Programmable surge protector with LAN interface.

    This driver was tested on EG-PMS2-LAN device only but should be easy to support other devices.
    """

    host: str | None = field(default=None)
    password: str | None = field(default="1")
    slot: int = 1

    def login(self):
        """
        Log in to the programmable power switch.

        :return: True if login is successful, False otherwise.
        """
        login_url = f"{self.base_url}/login.html"
        try:
            response = requests.post(login_url, data={"pw": self.password}, timeout=10)
            return response.status_code == 200
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.RequestException) as e:
            self.logger.error(f"Login failed: {str(e)}")
            return False

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        # Programmable power switch initialitzation. The EG-PMS2-LAN device has up to 4 slots.
        if self.slot < 1 or self.slot > 4:
            raise ValueError("Slot must be between 1 and 4")
        if self.host is None:
            raise ValueError("Host must be specified")
        self.logger.debug(f"Using Host: {self.host}, Slot: {self.slot}")
        self.base_url = f"http://{self.host}"


    def set_switch(self, switch_number, state):
        """
        Set the state of a specific switch.

        :param switch_number: The switch number (1, 2, etc.).
        :param state: The state to set (1 for ON, 0 for OFF).
        :return: True if the operation is successful, False otherwise.
        """
        if state not in [0, 1]:
            self.logger.error(f"Invalid state: {state}")
            return False

        if self.login():
            self.logger.debug("Login successful!")
        else:
            self.logger.debug("Login failed!")
            return False
        data = {f"cte{switch_number}": state}
        try:
            response = requests.post(self.base_url, data=data, timeout=10)
            if response.status_code != 200:
                self.logger.error(f"Set switch {switch_number} to {state} state failed!")
                return False
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.RequestException) as e:
            self.logger.error(f"Set switch failed: {str(e)}")
            return False

        self.logger.debug(f"Set switch {switch_number} to {state} state")

        return True

    @export
    def on(self) -> None:
        self.set_switch(self.slot, 1)

    @export
    def off(self) -> None:
        self.set_switch(self.slot, 0)

    @export
    def read(self) -> AsyncGenerator[PowerReading, None]:
        raise NotImplementedError
