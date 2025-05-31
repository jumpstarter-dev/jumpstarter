from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


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
    avd_arch: Optional[str] = None
    cores: Optional[int] = Field(default=4, ge=1)
    memory: int = Field(default=2048, ge=1024, le=16384)
    id: Optional[str] = None

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
    datadir: Optional[str] = None
    image: Optional[str] = None  # obsolete, use system instead
    initdata: Optional[str] = None

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
    snapstorage: Optional[str] = None
    no_snapstorage: bool = False
    snapshot_list: bool = False

    # Display and GPU
    no_window: bool = True
    gpu: Literal["auto", "host", "swiftshader", "angle", "guest"] = "auto"
    no_boot_anim: bool = False
    skin: Optional[str] = None
    skindir: Optional[str] = None
    no_skin: bool = False
    dpi_device: Optional[int] = Field(default=None, ge=0)
    fixed_scale: bool = False
    scale: str = Field(default="1", pattern=r"^[0-9]+(\.[0-9]+)?$")
    vsync_rate: Optional[int] = Field(default=None, ge=1)
    qt_hide_window: bool = False
    multidisplay: List[Tuple[int, int, int, int, int]] = []
    no_location_ui: bool = False
    no_hidpi_scaling: bool = False
    no_mouse_reposition: bool = False
    virtualscene_poster: Dict[str, str] = {}
    guest_angle: bool = False
    window_size: Optional[str] = Field(default=None, pattern=r"^\d+x\d+$")
    screen: Optional[str] = None
    use_host_vulkan: bool = False
    share_vid: bool = False
    hotplug_multi_display: bool = False

    # Network Configuration
    wifi_client_port: Optional[int] = Field(default=None, ge=1, le=65535)
    wifi_server_port: Optional[int] = Field(default=None, ge=1, le=65535)
    net_tap: Optional[str] = None
    net_tap_script_up: Optional[str] = None
    net_tap_script_down: Optional[str] = None
    net_socket: Optional[str] = None
    dns_server: Optional[str] = None
    http_proxy: Optional[str] = None
    netdelay: Literal["none", "umts", "gprs", "edge", "hscsd"] = "none"
    netspeed: Literal["full", "gsm", "hscsd", "gprs", "edge", "umts"] = "full"
    port: int = Field(default=5554, ge=5554, le=5682)
    ports: Optional[str] = None
    netfast: bool = False
    shared_net_id: Optional[int] = None
    wifi_tap: Optional[str] = None
    wifi_tap_script_up: Optional[str] = None
    wifi_tap_script_down: Optional[str] = None
    wifi_socket: Optional[str] = None
    vmnet_bridged: Optional[str] = None
    vmnet_shared: bool = False
    vmnet_start_address: Optional[str] = Field(default=None, pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    vmnet_end_address: Optional[str] = Field(default=None, pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    vmnet_subnet_mask: Optional[str] = Field(default=None, pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    vmnet_isolated: bool = False
    wifi_user_mode_options: Optional[str] = None
    network_user_mode_options: Optional[str] = None
    wifi_mac_address: Optional[str] = Field(default=None, pattern=r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    no_ethernet: bool = False

    # Audio Configuration
    no_audio: bool = False
    audio: Optional[str] = None
    allow_host_audio: bool = False
    radio: Optional[str] = None

    # Camera Configuration
    camera_back: Literal["emulated", "webcam0", "none"] = "emulated"
    camera_front: Literal["emulated", "webcam0", "none"] = "emulated"
    legacy_fake_camera: bool = False
    camera_hq_edge: bool = False

    # Localization
    timezone: Optional[str] = None
    change_language: Optional[str] = None
    change_country: Optional[str] = None
    change_locale: Optional[str] = None

    # Security
    encryption_key: Optional[str] = None
    selinux: Optional[Literal["enforcing", "permissive", "disabled"]] = None
    skip_adb_auth: bool = False

    # Hardware Acceleration
    accel: Literal["auto", "off", "on"] = "auto"
    no_accel: bool = False
    engine: Literal["auto", "qemu", "swiftshader"] = "auto"
    ranchu: bool = False
    cpu_delay: Optional[int] = None

    # Debugging and Monitoring
    verbose: bool = False
    show_kernel: bool = False
    logcat: Optional[str] = None
    logcat_output: Optional[str] = None
    debug_tags: Optional[str] = None
    tcpdump: Optional[str] = None
    detect_image_hang: bool = False
    save_path: Optional[str] = None
    metrics_to_console: bool = False
    metrics_collection: bool = False
    metrics_to_file: Optional[str] = None
    no_metrics: bool = False
    perf_stat: Optional[str] = None
    no_nested_warnings: bool = False
    no_direct_adb: bool = False
    check_snapshot_loadable: Optional[str] = None

    # gRPC Configuration
    grpc_port: Optional[int] = Field(default=None, ge=1, le=65535)
    grpc_tls_key: Optional[str] = None
    grpc_tls_cert: Optional[str] = None
    grpc_tls_ca: Optional[str] = None
    grpc_use_token: bool = False
    grpc_use_jwt: bool = True
    grpc_allowlist: Optional[str] = None
    idle_grpc_timeout: Optional[int] = None
    grpc_ui: bool = False

    # Advanced System Configuration
    acpi_config: Optional[str] = None
    append_userspace_opt: Dict[str, str] = {}
    feature: Dict[str, bool] = {}
    icc_profile: Optional[str] = None
    sim_access_rules_file: Optional[str] = None
    phone_number: Optional[str] = Field(default=None, pattern=r"^\+[0-9]{10,15}$")
    usb_passthrough: Optional[List[int]] = None
    waterfall: Optional[str] = None
    restart_when_stalled: bool = False
    wipe_data: bool = False
    delay_adb: bool = False
    quit_after_boot: Optional[int] = Field(default=None, ge=0)
    android_serialno: Optional[str] = None
    systemui_renderer: Optional[str] = None

    # QEMU Configuration
    qemu_args: List[str] = []
    props: Dict[str, str] = {}
    adb_path: Optional[str] = None

    # Additional environment variables
    env: Dict[str, str] = {}

    class Config:
        validate_assignment = True
