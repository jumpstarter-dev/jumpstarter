import os
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class FileAddress(BaseModel):
    file: str
    address: str


class DtbVariant(BaseModel):
    bootcmd: None | str = None
    file: None | str = None


class Dtb(BaseModel):
    default: str
    address: str
    variants: dict[str, DtbVariant]


class FlasherLogin(BaseModel):
    login_prompt: str
    username: str | None = None
    password: str | None = None
    prompt: str


class FlashBundleSpecV1Alpha1(BaseModel):
    manufacturer: str
    link: None | str
    bootcmd: str
    shelltype: Literal["busybox"] = Field(default="busybox")
    login: FlasherLogin = Field(default_factory=lambda: FlasherLogin(login_prompt="login:", prompt="#"))
    default_target: str
    targets: dict[str, str]
    kernel: FileAddress
    initram: None | FileAddress = None
    dtb: None | Dtb = None
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

        # if no variant is provided, use the default variant name from the manifest
        if not variant:
            variant = self.spec.dtb.default

        # look for the variant struct in this manifest
        variant_struct = self.spec.dtb.variants.get(variant)
        if variant_struct:
            return variant_struct.file
        else:
            raise ValueError(f"DTB variant {variant} not found in the manifest.")

    def get_boot_cmd(self, variant: str | None = None) -> str:
        if not self.spec.dtb:
            return self.spec.bootcmd
        # if no variant is provided, use the default variant name from the manifest
        if not variant:
            variant = self.spec.dtb.default
        # look for the variant struct in this manifest
        variant_struct = self.spec.dtb.variants.get(variant)
        if variant_struct:
            # If variant has a custom bootcmd, use it; otherwise fall back to default
            if variant_struct.bootcmd:
                return variant_struct.bootcmd
            else:
                return self.spec.bootcmd
        else:
            raise ValueError(f"DTB variant {variant} not found in the manifest.")

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
