"""
Base metaclass and class for Jumpstarter driver interfaces.

DriverInterfaceMeta enforces interface contracts at class-definition time
and maintains a registry for interface discovery. DriverInterface is the
base class that all driver interfaces should inherit from.
"""

from abc import ABCMeta, abstractmethod
from typing import ClassVar


class DriverInterfaceMeta(ABCMeta):
    """Metaclass for Jumpstarter driver interfaces.

    Enforces:
    - client() classmethod must be defined and return str
    Provides:
    - Interface registry for jmp interface generate-all
    - Unambiguous discovery for build_file_descriptor()
    """

    _registry: ClassVar[dict[str, type]] = {}

    def __new__(mcs, name, bases, namespace, **kwargs):
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)

        # Skip base infrastructure classes
        _SKIP_CLASSES = {"DriverInterface", "Driver", "Proxy"}
        if name in _SKIP_CLASSES:
            return cls

        # Only register classes that define their own client() in their
        # namespace (not inherited). This covers both abstract interfaces
        # and concrete drivers with client().
        is_concrete_interface = "client" in namespace

        if is_concrete_interface:
            # Validate client() classmethod
            client_method = namespace.get("client")
            if client_method is None:
                raise TypeError(
                    f"{name} must define a client() classmethod "
                    f"returning the import path of the client class"
                )

            # Register the interface
            mcs._registry[f"{cls.__module__}.{cls.__qualname__}"] = cls

        return cls


class DriverInterface(metaclass=DriverInterfaceMeta):
    """Base class for all Jumpstarter driver interfaces.

    Subclass this to define a driver interface contract. All methods
    (except client()) must be @abstractmethod with full type annotations.

    Required:
        client(): classmethod returning the client import path

    Optional:
        __interface_name__: short name for proto package (e.g., "power").
            Defaults to the class name with "Interface" suffix stripped,
            lowercased.
    """

    __interface_name__: ClassVar[str | None] = None

    @classmethod
    @abstractmethod
    def client(cls) -> str:
        """Return the full import path of the corresponding client class."""
        ...
