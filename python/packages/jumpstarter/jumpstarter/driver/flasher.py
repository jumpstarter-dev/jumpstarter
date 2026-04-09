"""
Common flasher interface for drivers that flash images to devices.

This is a pure ABC with no external dependencies, providing a common interface
for flasher drivers across the jumpstarter ecosystem.
"""

from __future__ import annotations

from abc import ABCMeta, abstractmethod


class FlasherInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.flasher.FlasherClient"

    @abstractmethod
    def flash(self, source, target: str | None = None): ...

    @abstractmethod
    def dump(self, target, partition: str | None = None): ...
