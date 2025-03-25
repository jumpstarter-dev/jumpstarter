from typing import Generic, Literal, TypeVar

from pydantic import Field

from .json import JsonBaseModel

T = TypeVar("T")


class V1Alpha1List(JsonBaseModel, Generic[T]):
    """A generic list result type."""

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    items: list[T]
    kind: Literal["List"] = Field(default="List")
