import os
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, model_validator


class AdbOptions(BaseModel):
    """
    Holds the options for the ADB server.

    Attributes:
        host (str): The host address for the ADB server. Default is
    """

    adb_path: str = Field(default="adb")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=5037)


class EmulatorOptions(BaseModel):
    """
    Pydantic model for Android Emulator CLI arguments.
    See original docstring for full documentation.
    """

    # Core Configuration
    emulator_path: str = Field(default="emulator")
    avd: str = Field(default="default")
    cores: Optional[int] = Field(default=4, ge=1)
    memory: int = Field(default=2048, ge=1024, le=16384)

    # System Images and Storage
    sysdir: Optional[str] = None
    system: Optional[str] = None
    vendor: Optional[str] = None
    kernel: Optional[str] = None
    ramdisk: Optional[str] = None
    data: Optional[str] = None
    sdcard: Optional[str] = None
    partition_size: int = Field(default=2048, ge=512, le=16384)
    writable_system: bool = False

    # Cache Configuration
    cache: Optional[str] = None
    cache_size: Optional[int] = Field(default=None, ge=16)
    no_cache: bool = False

    # Snapshot Management
    no_snapshot: bool = False
    no_snapshot_load: bool = False
    no_snapshot_save: bool = False
    snapshot: Optional[str] = None
    force_snapshot_load: bool = False
    no_snapshot_update_time: bool = False
    qcow2_for_userdata: bool = False

    # Display and GPU
    no_window: bool = True
    gpu: Literal["auto", "host", "swiftshader", "angle", "guest"] = "auto"
    gpu_mode: Literal["auto", "host", "swiftshader", "angle", "guest"] = "auto"
    no_boot_anim: bool = False
    skin: Optional[str] = None
    dpi_device: Optional[int] = Field(default=None, ge=0)
    fixed_scale: bool = False
    scale: str = "1"
    vsync_rate: Optional[int] = Field(default=None, ge=1)
    qt_hide_window: bool = False
    multidisplay: List[Tuple[int, int, int, int, int]] = []
    no_location_ui: bool = False
    no_hidpi_scaling: bool = False
    no_mouse_reposition: bool = False
    virtualscene_poster: Dict[str, str] = {}
    guest_angle: bool = False

    # Network Configuration
    wifi_client_port: Optional[int] = Field(default=None, ge=1, le=65535)
    wifi_server_port: Optional[int] = Field(default=None, ge=1, le=65535)
    net_tap: Optional[str] = None
    net_tap_script_up: Optional[str] = None
    net_tap_script_down: Optional[str] = None
    dns_server: Optional[str] = None
    http_proxy: Optional[str] = None
    netdelay: Literal["none", "umts", "gprs", "edge", "hscsd"] = "none"
    netspeed: Literal["full", "gsm", "hscsd", "gprs", "edge", "umts"] = "full"
    port: int = Field(default=5554, ge=5554, le=5682)

    # Audio Configuration
    no_audio: bool = False
    audio: Optional[str] = None
    allow_host_audio: bool = False

    # Camera Configuration
    camera_back: Literal["emulated", "webcam0", "none"] = "emulated"
    camera_front: Literal["emulated", "webcam0", "none"] = "emulated"

    # Localization
    timezone: Optional[str] = None
    change_language: Optional[str] = None
    change_country: Optional[str] = None
    change_locale: Optional[str] = None

    # Security
    encryption_key: Optional[str] = None
    selinux: Optional[Literal["enforcing", "permissive", "disabled"]] = None

    # Hardware Acceleration
    accel: Literal["auto", "off", "on"] = "auto"
    no_accel: bool = False
    engine: Literal["auto", "qemu", "swiftshader"] = "auto"

    # Debugging and Monitoring
    verbose: bool = False
    show_kernel: bool = False
    logcat: Optional[str] = None
    debug_tags: Optional[str] = None
    tcpdump: Optional[str] = None
    detect_image_hang: bool = False
    save_path: Optional[str] = None

    # gRPC Configuration
    grpc_port: Optional[int] = Field(default=None, ge=1, le=65535)
    grpc_tls_key: Optional[str] = None
    grpc_tls_cert: Optional[str] = None
    grpc_tls_ca: Optional[str] = None
    grpc_use_token: bool = False
    grpc_use_jwt: bool = True

    # Advanced System Configuration
    acpi_config: Optional[str] = None
    append_userspace_opt: Dict[str, str] = {}
    feature: Dict[str, bool] = {}
    icc_profile: Optional[str] = None
    sim_access_rules_file: Optional[str] = None
    phone_number: Optional[str] = None
    usb_passthrough: Optional[Tuple[int, int, int, int]] = None
    waterfall: Optional[str] = None
    restart_when_stalled: bool = False
    wipe_data: bool = False
    delay_adb: bool = False
    quit_after_boot: Optional[int] = Field(default=None, ge=0)

    # QEMU Configuration
    qemu_args: List[str] = []
    props: Dict[str, str] = {}

    # Additional environment variables
    env: Dict[str, str] = {}

    @model_validator(mode="after")
    def validate_paths(self) -> "EmulatorOptions":
        path_fields = [
            "sysdir",
            "system",
            "vendor",
            "kernel",
            "ramdisk",
            "data",
            "encryption_key",
            "cache",
            "net_tap_script_up",
            "net_tap_script_down",
            "icc_profile",
            "sim_access_rules_file",
            "grpc_tls_key",
            "grpc_tls_cert",
            "grpc_tls_ca",
            "acpi_config",
            "save_path",
        ]

        for name in path_fields:
            path = getattr(self, name)
            if path and not os.path.exists(path):
                raise ValueError(f"Path does not exist: {path}")

        # Validate virtual scene poster paths
        for _, path in self.virtualscene_poster.items():
            if not os.path.exists(path):
                raise ValueError(f"Virtual scene poster image not found: {path}")
            if not path.lower().endswith((".png", ".jpg", ".jpeg")):
                raise ValueError(f"Virtual scene poster must be a PNG or JPEG file: {path}")

        # Validate phone number format if provided
        if self.phone_number is not None and not self.phone_number.replace("+", "").replace("-", "").isdigit():
            raise ValueError("Phone number must contain only digits, '+', or '-'")

        return self

    class Config:
        validate_assignment = True
