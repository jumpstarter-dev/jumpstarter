from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Any


class FlasherInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.flasher.FlasherClient"

    @abstractmethod
    def flash(self, source: Any, target: str | None = None) -> None: ...

    @abstractmethod
    def dump(self, target: Any, partition: str | None = None) -> None: ...
