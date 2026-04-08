"""Tests for DriverInterfaceMeta and DriverInterface (Phase 1a)."""

from abc import abstractmethod

import pytest

from .interface import DriverInterface, DriverInterfaceMeta


class TestDriverInterfaceMeta:
    """Tests for the DriverInterfaceMeta metaclass."""

    def test_base_class_not_registered(self):
        """DriverInterface itself should not appear in the registry."""
        fqn = f"{DriverInterface.__module__}.{DriverInterface.__qualname__}"
        assert fqn not in DriverInterfaceMeta._registry

    def test_concrete_interface_registered(self):
        """A concrete interface defining client() should be registered."""

        class MyInterface(DriverInterface):
            @classmethod
            def client(cls) -> str:
                return "some.module.MyClient"

            @abstractmethod
            async def do_thing(self) -> None: ...

        fqn = f"{MyInterface.__module__}.{MyInterface.__qualname__}"
        assert fqn in DriverInterfaceMeta._registry
        assert DriverInterfaceMeta._registry[fqn] is MyInterface

    def test_intermediate_abstract_not_registered(self):
        """An intermediate base that doesn't define client() is skipped."""

        class BaseInterface(DriverInterface):
            @classmethod
            def client(cls) -> str:
                return "some.module.Client"

        class ExtendedInterface(BaseInterface):
            """Extends BaseInterface without redefining client()."""

            @abstractmethod
            async def extra_method(self) -> None: ...

        fqn_extended = f"{ExtendedInterface.__module__}.{ExtendedInterface.__qualname__}"
        # ExtendedInterface doesn't define client() in its own namespace,
        # so it should NOT be separately registered
        assert fqn_extended not in DriverInterfaceMeta._registry

    def test_registry_key_is_module_qualified(self):
        """Registry keys use module.qualname format."""

        class QualifiedInterface(DriverInterface):
            @classmethod
            def client(cls) -> str:
                return "some.module.Client"

        expected_key = f"{QualifiedInterface.__module__}.{QualifiedInterface.__qualname__}"
        assert expected_key in DriverInterfaceMeta._registry


class TestDriverInterface:
    """Tests for the DriverInterface base class."""

    def test_client_is_abstract(self):
        """client() must be abstract — cannot instantiate DriverInterface directly."""
        with pytest.raises(TypeError):
            DriverInterface()

    def test_subclass_with_client_can_be_used(self):
        """A subclass providing client() should define it as a classmethod."""

        class ConcreteInterface(DriverInterface):
            @classmethod
            def client(cls) -> str:
                return "my.module.ConcreteClient"

        assert ConcreteInterface.client() == "my.module.ConcreteClient"

    def test_metaclass_is_driver_interface_meta(self):
        """DriverInterface should use DriverInterfaceMeta as its metaclass."""
        assert type(DriverInterface) is DriverInterfaceMeta
