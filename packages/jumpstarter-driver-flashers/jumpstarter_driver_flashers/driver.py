from dataclasses import dataclass, field
from pathlib import Path

import anyio.to_thread
from jumpstarter_driver_http.driver import HttpServer
from jumpstarter_driver_tftp.driver import Tftp
from jumpstarter_driver_uboot.driver import UbootConsole
from oras.provider import Registry

from .bundle import FlasherBundleManifestV1Alpha1
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class BaseFlasher(Driver):
    """driver for Jumpstarter"""

    flasher_bundle: str = field(default="quay.io/jumpstarter-dev/jumpstarter-flasher-test:latest")
    variant: None | str = field(default=None)
    manifest: str = field(default="manifest.yaml")
    cache_dir: str = field(default="/var/lib/jumpstarter/flasher")
    tftp_dir: str = field(default="/var/lib/tftpboot")
    http_dir: str = field(default="/var/www/html")

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        # Ensure required children are present if not already instantiated
        # in configuration
        if "tftp" not in self.children:
            self.children["tftp"] = Tftp(root_dir=self.tftp_dir)
        self.tftp = self.children["tftp"]

        if "http" not in self.children:
            self.children["http"] = HttpServer(root_dir=self.http_dir)
        self.http = self.children["http"]

        # Ensure required children are present, the following are not auto-created
        if "serial" not in self.children:
            raise ConfigurationError(
                "'serial' instance is required for BaseFlasher either via a ref ir a direct child instance"
            )

        if "power" not in self.children:
            raise ConfigurationError(
                "'power' instance is required for BaseFlasher either via a ref ir a direct child instance"
            )

        if "uboot" not in self.children:
            self.children["uboot"] = UbootConsole(
                children={
                    "power": self.children["power"],
                    "serial": self.children["serial"],
                }
            )

        # bundles that have already been downloaded in the current session
        self._downloaded = {}

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_flashers.client.BaseFlasherClient"

    @export
    async def setup_flasher_bundle(self, force_flash_bundle: str | None = None):
        """Setup flasher bundle

        This method sets all the files in place in the tftp server
        so that the target can download from bootloader.
        """

        # the client is requesting a different flasher bundle
        if force_flash_bundle:
            self.flasher_bundle = force_flash_bundle

        manifest = await self.get_flasher_manifest()
        kernel_path = await self._get_file_path(manifest.spec.kernel.file)
        self.logger.info(f"Setting up kernel in tftp: {kernel_path}")
        await self.tftp.storage.copy_exporter_file(kernel_path, kernel_path.name)

        initram_path = await self._get_file_path(manifest.spec.initram.file) if manifest.spec.initram else None
        if initram_path:
            self.logger.info(f"Setting up initram in tftp: {initram_path}")
            await self.tftp.storage.copy_exporter_file(initram_path, initram_path.name)

        dtb_path = await self._get_file_path(manifest.get_dtb_file(self.variant)) if manifest.spec.dtb else None
        if dtb_path:
            self.logger.info(f"Setting up dtb in tftp: {dtb_path}")
            await self.tftp.storage.copy_exporter_file(dtb_path, dtb_path.name)

    @export
    def set_dtb(self, handle):
        """Provide a different dtb from client"""
        raise NotImplementedError

    @export
    async def use_dtb_variant(self, variant):
        """Provide a different dtb reference from the flasher bundle"""
        manifest = await self.get_flasher_manifest()
        # Check if the variant exists in the manifest
        if not manifest.spec.dtb or variant not in manifest.spec.dtb.variants:
            variant_list = []
            if manifest.spec.dtb:
                variant_list = list(manifest.spec.dtb.variants.keys())
            raise ValueError(
                f"DTB variant {variant} not found in the flasher bundle, "
                f"available variants are: {variant_list}."
            )
        self.variant = variant

    def set_kernel(self, handle):
        """Provide a different kernel from client"""
        raise NotImplementedError

    def set_initram(self, handle):
        """Provide a different initram from client"""
        raise NotImplementedError

    def _download_to_cache(self) -> str:
        """Download the bundle to the cache

        This function downloads the bundle contents to the cache directory,
        if it was already downloaded during this session we don't download it again.
        """
        if self._downloaded.get(self.flasher_bundle):
            self.logger.debug(f"Bundled already downloaded: {self.flasher_bundle}")
            return self._downloaded[self.flasher_bundle]

        oras_client = Registry()
        self.logger.info(f"Downloading bundle: {self.flasher_bundle}")

        # make a filesystem valid name for the cache directory
        bundle_subdir = self.flasher_bundle.replace(":", "_").replace("/", "_")
        bundle_dir = Path(self.cache_dir) / bundle_subdir

        # ensure the bundle dir exists
        bundle_dir.mkdir(parents=True, exist_ok=True)
        oras_client.pull(self.flasher_bundle, outdir=bundle_dir)

        self.logger.info(f"Bundle downloaded to {bundle_dir}")

        # mark this bundle as downloaded for the current object lifetime
        self._downloaded[self.flasher_bundle] = bundle_dir
        return bundle_dir

    async def _get_file_path(self, filename) -> Path:
        """Get the bundle contents path.

        This function will ensure that the bundle is downloaded into cache, and
        then return the path to the requested file in the cache directory.
        """
        bundle_dir = await anyio.to_thread.run_sync(self._download_to_cache)
        return Path(bundle_dir) / filename

    @export
    async def get_flasher_manifest_yaml(self) -> str:
        """Return the manifest yaml as a string for client side consumption"""
        with open(await self._get_file_path(self.manifest)) as f:
            return f.read()

    async def get_flasher_manifest(self) -> FlasherBundleManifestV1Alpha1:
        filename = await self._get_file_path(self.manifest)
        return FlasherBundleManifestV1Alpha1.from_file(filename)

    @export
    async def get_kernel_filename(self) -> str:
        """Return the kernel filename"""
        manifest = await self.get_flasher_manifest()
        return Path(manifest.get_kernel_file()).name

    @export
    async def get_initram_filename(self) -> str | None:
        """Return the initram filename"""
        manifest = await self.get_flasher_manifest()
        filename = manifest.get_initram_file()
        if filename:
            return Path(filename).name

    @export
    async def get_dtb_filename(self) -> str:
        """Return the dtb filename"""
        manifest = await self.get_flasher_manifest()
        dtb_file = manifest.get_dtb_file(self.variant)
        if dtb_file:
            return Path(manifest.get_dtb_file(self.variant)).name
        else:
            return ""

    @export
    async def get_dtb_address(self) -> str:
        """Return the dtb address"""
        manifest = await self.get_flasher_manifest()
        return manifest.get_dtb_address()

    @export
    async def get_kernel_address(self) -> str:
        """Return the kernel address"""
        manifest = await self.get_flasher_manifest()
        return manifest.get_kernel_address()

    @export
    async def get_initram_address(self) -> str:
        """Return the initram address"""
        manifest = await self.get_flasher_manifest()
        return manifest.get_initram_address()

    @export
    async def get_bootcmd(self) -> str:
        """Return the bootcmd"""
        manifest = await self.get_flasher_manifest()
        return manifest.get_boot_cmd(self.variant)

@dataclass(kw_only=True)
class TIJ784S4Flasher(BaseFlasher):
    """driver for Jumpstarter"""

    flasher_bundle: str = "quay.io/jumpstarter-dev/jumpstarter-flasher-ti-j784s4:latest"


@dataclass(kw_only=True)
class RCarS4Flasher(BaseFlasher):
    """RCarS4 driver for Jumpstarter"""

    flasher_bundle: str = "quay.io/jumpstarter-dev/jumpstarter-flasher-rcar-s4:latest"
