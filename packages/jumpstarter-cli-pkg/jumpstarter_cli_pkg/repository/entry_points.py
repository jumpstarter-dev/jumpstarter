import importlib
import inspect
from importlib.metadata import EntryPoint
from typing import Any, Literal, Optional

from pydantic import Field

from jumpstarter.common.exceptions import JumpstarterException
from jumpstarter.models import JsonBaseModel, ListBaseModel


class EntryPointGroups:
    DRIVER_ENTRY_POINT_GROUP = "jumpstarter.drivers"
    DRIVER_CLIENT_ENTRY_POINT_GROUP = "jumpstarter.clients"
    ADAPTER_ENTRY_POINT_GROUP = "jumpstarter.adapters"


def load_entrypoint_class(entry_point: EntryPoint) -> Any:
    """
    Load a class specified by an EntryPoint.

    Args:
        entry_point: An importlib.metadata.EntryPoint object

    Returns:
        The class object

    Raises:
        JumpstarterException: If there's an error loading the class
    """
    try:
        # Load the module specified by the entry point
        module_name, class_name = entry_point.value.split(":")
        # This could be potentiall unsafe!
        module = importlib.import_module(module_name)

        # Get the class from the module
        return getattr(module, class_name)
    except (ImportError, AttributeError, ValueError) as e:
        raise JumpstarterException(f"Error loading entry point '{entry_point.value}'") from e


def get_driver_client_type(cls: Any) -> Optional[str]:
    """
    Get the client type string from a driver's abstract class method.

    Args:
        cls: The class object

    Returns:
        The string returned by the client() method if it exists, None otherwise

    Raises:
        JumpstarterException: If there's an error calling the client method
    """
    try:
        if hasattr(cls, "client") and callable(cls.client):
            return cls.client()
        return None
    except Exception as e:
        raise JumpstarterException(f"Error calling client method on '{inspect.getmodulename(cls)}'") from e


def get_driver_client_has_cli(cls: Any) -> Optional[str]:
    """
    Check if a driver client has a CLI defined.

    Args:
        cls: The class object

    Returns:
        `True` if the client has a CLI, `False` otherwise

    Raises:
        JumpstarterException: If there's an error checking the client method
    """
    try:
        if hasattr(cls, "cli") and callable(cls.cli):
            return True
        return False
    except Exception as e:
        raise JumpstarterException(f"Error checking for CLI on class '{inspect.getmodulename(cls)}'") from e


def get_entrypoint_class_summary(cls: Any) -> Optional[str]:
    """
    Extract the documentation string summary from a class specified by an EntryPoint.

    Args:
        cls: The class object

    Returns:
        The docstring of the class specified by the EntryPoint's value,
        or None if no docstring is found.
    """
    try:
        # Ignore classes without real doc strings
        if cls.__doc__ is None:
            return None
        # Extract and attempt to parse the doc string
        docstring = inspect.getdoc(cls)
        if docstring is not None:
            # Just return the first line of the docstring
            return docstring.split("\n")[0].strip()
        return None
    except ValueError as e:
        raise JumpstarterException(f"Error getting docstring for '{inspect.getmodulename(cls)}'") from e


class V1Alpha1AdapterEntryPoint(JsonBaseModel):
    """
    A Jumpstarter adapter entry point.
    """

    _entry_point: EntryPoint

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1", alias="apiVersion")
    kind: Literal["AdapterEntryPoint"] = Field(default="AdapterEntryPoint")

    name: str
    module: str
    function_name: str = Field(alias="functionName")
    type: str
    package: str
    summary: Optional[str] = None

    @staticmethod
    def from_entry_point(ep: EntryPoint, should_inspect: bool = False):
        summary = None
        if should_inspect:
            summary = get_entrypoint_class_summary(load_entrypoint_class(ep))
        value_split = ep.value.split(":")
        module = value_split[0]
        function_name = value_split[1]
        adapter = V1Alpha1AdapterEntryPoint(
            name=ep.name,
            module=module,
            function_name=function_name,
            type=ep.value.replace(":", "."),
            package=ep.dist.name,
            summary=summary,
        )
        adapter._entry_point = ep
        return adapter


class V1Alpha1DriverClientEntryPoint(JsonBaseModel):
    """
    A Jumpstarter driver client entry point.
    """

    _entry_point: EntryPoint

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1", alias="apiVersion")
    kind: Literal["DriverClientEntryPoint"] = Field(default="DriverClientEntryPoint")

    name: str
    type: str
    module: str
    class_name: str = Field(alias="className")
    package: str
    summary: Optional[str] = Field(default=None)
    cli: bool = False

    @staticmethod
    def from_entry_point(ep: EntryPoint, should_inspect: bool = False):
        summary = None
        cli = False
        # Inspect the driver client class to get additional metadata
        if should_inspect:
            cls = load_entrypoint_class(ep)
            summary = get_entrypoint_class_summary(cls)
            cli = get_driver_client_has_cli(cls)
        value_split = ep.value.split(":")
        module = value_split[0]
        class_name = value_split[1]
        driver_client = V1Alpha1DriverClientEntryPoint(
            name=ep.name,
            module=module,
            class_name=class_name,
            type=ep.value.replace(":", "."),
            package=ep.dist.name,
            summary=summary,
            cli=cli,
        )
        driver_client._entry_point = ep
        return driver_client


class V1Alpha1DriverEntryPoint(JsonBaseModel):
    """
    A Jumpstarter driver entry point.
    """

    _entry_point: EntryPoint

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1", alias="apiVersion")
    kind: Literal["DriverEntryPoint"] = Field(default="DriverEntryPoint")

    name: str
    type: str
    module: str
    class_name: str = Field(alias="className")
    package: str
    client_type: Optional[str] = Field(default=None, alias="clientType")
    summary: Optional[str] = None

    @staticmethod
    def from_entry_point(ep: EntryPoint, should_inspect: bool = False):
        """
        Construct a driver entry point from an importlib.metadata.EntryPoint.

        Args:
            ep: The EntryPoint object
            should_inspect: Preform additional inspection of the EntryPoint class (default = False)

        Returns:
            The constructed V1Alpha1DriverEntryPoint
        """
        summary = None
        client_type = None
        # Inspect the driver class to get additional metadata
        if should_inspect:
            cls = load_entrypoint_class(ep)
            summary = get_entrypoint_class_summary(cls)
            client_type = get_driver_client_type(cls)
        # Return the constructed driver
        value_split = ep.value.split(":")
        module = value_split[0]
        class_name = value_split[1]
        driver = V1Alpha1DriverEntryPoint(
            name=ep.name,
            module=module,
            class_name=class_name,
            type=ep.value.replace(":", "."),
            package=ep.dist.name,
            summary=summary,
            client_type=client_type,
        )
        driver._entry_point = ep
        return driver


class V1Alpha1AdapterEntryPointList(ListBaseModel[V1Alpha1AdapterEntryPoint]):
    """
    A list of Jumpstarter adapter list models.
    """

    kind: Literal["AdapterEntryPointList"] = Field(default="AdapterEntryPointList")


class V1Alpha1DriverEntryPointList(ListBaseModel[V1Alpha1DriverEntryPoint]):
    """
    A list of Jumpstarter driver list models.
    """

    kind: Literal["DriverEntryPointList"] = Field(default="DriverEntryPointList")


class V1Alpha1DriverClientEntryPointList(ListBaseModel[V1Alpha1DriverClientEntryPoint]):
    """
    A list of Jumpstarter driver client classes.
    """

    kind: Literal["DriverClientEntryPointList"] = Field(default="DriverClientEntryPointList")
