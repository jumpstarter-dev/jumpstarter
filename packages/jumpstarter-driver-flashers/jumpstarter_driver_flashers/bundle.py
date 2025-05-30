import os
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


class FileAddress(BaseModel):
    file: str
    address: str


class DtbVariant(BaseModel):
    default: str
    address: str
    variants: dict[str, str]


class FlasherLogin(BaseModel):
    login_prompt: str
    username: str | None = None
    password: str | None = None
    prompt: str


class FlashBundleSpecV1Alpha1(BaseModel):
    manufacturer: str
    link: Optional[str]
    bootcmd: str
    shelltype: Literal["busybox"] = Field(default="busybox")
    login: FlasherLogin = Field(default_factory=lambda: FlasherLogin(login_prompt="login:", prompt="#"))
    default_target: str
    targets: dict[str, str]
    kernel: FileAddress
    initram: Optional[FileAddress] = None
    dtb: Optional[DtbVariant] = None
    preflash_commands: list[str] = Field(default_factory=list)


class ObjectMeta(BaseModel):
    name: str


class FlasherBundleManifestV1Alpha1(BaseModel):
    apiVersion: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1")
    kind: Literal["FlashBundleManifest"] = Field(default="FlashBundleManifest")
    metadata: ObjectMeta
    spec: FlashBundleSpecV1Alpha1

    def get_dtb_address(self) -> str | None:
        if not self.spec.dtb:
            return None
        return self.spec.dtb.address

    def get_dtb_file(self, variant: str | None = None) -> str | None:
        if not self.spec.dtb:
            return None

        if not variant:
            variant = self.spec.dtb.default

        return self.spec.dtb.variants.get(variant)

    def get_kernel_address(self) -> str:
        return self.spec.kernel.address

    def get_kernel_file(self) -> str:
        return self.spec.kernel.file

    def get_initram_file(self) -> str | None:
        if not self.spec.initram:
            return None
        return self.spec.initram.file

    def get_initram_address(self) -> str | None:
        if not self.spec.initram:
            return None
        return self.spec.initram.address

    @classmethod
    def from_file(cls, path: os.PathLike):
        with open(path) as f:
            v = cls.model_validate(yaml.safe_load(f))
            return v

    @classmethod
    def from_string(cls, data: str):
        v = cls.model_validate(yaml.safe_load(data))
        return v
