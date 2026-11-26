from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from .members import iter_client_members
from jumpstarter.client import DriverClient


class GadgetClient(DriverClient):
    def on(self) -> None: ...

    def read(self) -> Generator[int, None, None]:
        yield 1

    @contextmanager
    def opened(self):
        yield self

    @property
    def label(self) -> str:
        return "gadget"

    async def background(self) -> None: ...

    def _private(self) -> None: ...


def test_member_kinds():
    kinds = {member.name: member.kind for member in iter_client_members(GadgetClient)}
    assert kinds == {
        "on": "method",
        "read": "generator",
        "opened": "contextmanager",
        "label": "property",
        "background": "async",
    }


def test_base_class_members_are_skipped():
    names = {member.name for member in iter_client_members(GadgetClient)}
    assert not names & {"call", "streamingcall", "close", "cli"}
    assert iter_client_members(DriverClient) == []


def test_function_and_owner():
    member = next(m for m in iter_client_members(GadgetClient) if m.name == "label")
    assert member.owner is GadgetClient
    assert member.function is GadgetClient.label.fget
