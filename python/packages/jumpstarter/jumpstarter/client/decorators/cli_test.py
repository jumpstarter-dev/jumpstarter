from typing import TYPE_CHECKING, ClassVar, cast

from jumpstarter.client.decorators import driver_click_group

if TYPE_CHECKING:
    from jumpstarter.client import DriverClient


class _FakeClient:
    description = None
    methods_description: ClassVar[dict] = {}


def test_command_accepts_bare_decorator():
    # Covers the bare @group.command branch in DriverClickGroup.command:
    # without it the command is silently dropped and never registered.
    @driver_click_group(cast("DriverClient", _FakeClient()))
    def base():
        """group"""

    @base.command
    def bare():
        pass

    @base.command()
    def paren():
        pass

    assert {"bare", "paren"} <= set(base.commands)
