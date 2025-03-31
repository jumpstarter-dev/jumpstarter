from typing import Generic, Literal, TypeVar

from pydantic import Field

from .model import JsonBaseModel

T = TypeVar("T")


class ListBaseModel(JsonBaseModel, Generic[T]):
    """
    A generic Pydantic list model result type.

    This class provides a standardized way to represent lists of objects in the Jumpstarter API.
    It follows the Kubernetes-style API convention with apiVersion, kind, and items fields.

    Examples:
        Basic usage with a simple Pydantic model:

        ```python
        from jumpstarter.models import JsonBaseModel, ListBaseModel

        class User(JsonBaseModel):
            name: str
            email: str

        class UserList(ListBaseModel[User]):
            kind: Literal["UserList"] = Field(default="UserList")
            pass
    """

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["List"] = Field(default="List")
    items: list[T] = []
