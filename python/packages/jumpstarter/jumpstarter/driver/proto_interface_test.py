"""ProtoInterface — the runtime base of codegen-generated proto-first driver interfaces.

A native driver subclasses ONLY the generated base (which IS a Driver): implementations of the
interface's declared methods are exported automatically (no @export), and @driver(client=...)
overrides the advertised client. NOTE: no `from __future__ import annotations` here — the
introspection needs real runtime annotations.
"""

from abc import abstractmethod
from collections.abc import AsyncIterator

from jumpstarter.driver import ProtoInterface, driver
from jumpstarter.driver.decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMING_DRIVERCALL,
)
from jumpstarter.driver.descriptor_builder import resolve_interface_class
from jumpstarter.driver.proto_marshal import build_marshaller


class FakeInterface(ProtoInterface):
    """Stand-in for a codegen-generated interface base."""

    @classmethod
    def client(cls) -> str:
        return "fake._generated.fake_client.FakeClient"

    @abstractmethod
    async def ping(self, message: str) -> str: ...

    @abstractmethod
    async def watch(self) -> AsyncIterator[str]: ...


class FakeDriver(FakeInterface):
    async def ping(self, message: str) -> str:
        return message.upper()

    async def watch(self) -> AsyncIterator[str]:
        yield "tick"

    def helper(self) -> None:
        """Not declared by the interface — must NOT be exported."""


def test_interface_method_impls_are_auto_exported():
    assert getattr(FakeDriver.ping, MARKER_DRIVERCALL, None) == MARKER_MAGIC
    assert getattr(FakeDriver.watch, MARKER_STREAMING_DRIVERCALL, None) == MARKER_MAGIC
    assert getattr(FakeDriver.helper, MARKER_DRIVERCALL, None) is None
    assert getattr(FakeDriver.helper, MARKER_STREAMING_DRIVERCALL, None) is None


def test_generated_base_is_a_full_driver():
    instance = FakeDriver()
    assert instance.client() == "fake._generated.fake_client.FakeClient"
    assert resolve_interface_class(instance) is FakeInterface

    marshaller = build_marshaller(instance)
    assert marshaller is not None
    assert marshaller.service_full_name == "jumpstarter.interfaces.fake.v1.FakeInterface"
    assert sorted(spec.grpc_path.rsplit("/", 1)[1] for spec in marshaller.methods.values()) == [
        "Ping",
        "Watch",
    ]


def test_driver_decorator_overrides_the_advertised_client():
    @driver(client="example.client.CustomClient")
    class CustomClientDriver(FakeInterface):
        async def ping(self, message: str) -> str:
            return message

        async def watch(self) -> AsyncIterator[str]:
            yield "tick"

    assert CustomClientDriver.client() == "example.client.CustomClient"
    # The interface (and thus the native contract) is still the generated base.
    assert resolve_interface_class(CustomClientDriver()) is FakeInterface


def test_subclass_overrides_are_exported_too():
    class Louder(FakeDriver):
        async def ping(self, message: str) -> str:
            return message.upper() + "!"

    assert getattr(Louder.__dict__["ping"], MARKER_DRIVERCALL, None) == MARKER_MAGIC
