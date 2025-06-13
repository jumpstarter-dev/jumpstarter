import os
import subprocess
import threading
from dataclasses import field
from subprocess import TimeoutExpired
from typing import IO, AsyncGenerator

from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.driver import PowerInterface
from pydantic.dataclasses import dataclass

from jumpstarter_driver_android.driver.device import AndroidDevice
from jumpstarter_driver_android.driver.options import EmulatorOptions

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class AndroidEmulator(AndroidDevice):
    """
    AndroidEmulator class provides an interface to configure and manage an Android Emulator instance.
    """

    emulator: EmulatorOptions = field(default_factory=EmulatorOptions)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        # Add the android emulator power driver
        self.children["power"] = AndroidEmulatorPower(parent=self)


@dataclass(kw_only=True)
class AndroidEmulatorPower(PowerInterface, Driver):
    parent: AndroidEmulator

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self._process = None
        self._log_thread = None
        self._stderr_thread = None

    def _process_logs(self, pipe: IO[bytes], is_stderr: bool = False) -> None:
        """Process logs from the emulator and redirect them to the Python logger."""
        try:
            for line in iter(pipe.readline, b""):
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                # Extract log level if present
                if "|" in line_str:
                    level_str, message = line_str.split("|", 1)
                    level_str = level_str.strip().upper()
                    message = message.strip()

                    # Map emulator log levels to Python logging levels
                    if "ERROR" in level_str or "FATAL" in level_str:
                        self.logger.error(message)
                    elif "WARN" in level_str:
                        self.logger.warning(message)
                    elif "DEBUG" in level_str:
                        self.logger.debug(message)
                    elif "INFO" in level_str:
                        self.logger.info(message)
                    else:
                        # Default to info for unknown levels
                        self.logger.info(line_str)
                else:
                    # If no level specified, use INFO for stdout and ERROR for stderr
                    if is_stderr:
                        self.logger.error(line_str)
                    else:
                        self.logger.info(line_str)
        except (ValueError, IOError) as e:
            self.logger.error(f"Error processing emulator logs: {e}")
        finally:
            pipe.close()

    def _make_emulator_command(self) -> list[str]:
        """Construct the command to start the Android emulator."""
        cmdline = [
            self.parent.emulator.emulator_path,
            "-avd",
            self.parent.emulator.avd,
        ]

        # Add emulator arguments from EmulatorArguments
        args = self.parent.emulator

        # Core Configuration
        cmdline += ["-avd-arch", args.avd_arch] if args.avd_arch else []
        cmdline += ["-id", args.id] if args.id else []
        cmdline += ["-cores", str(args.cores)] if args.cores else []
        cmdline += ["-memory", str(args.memory)] if args.memory else []

        # System Images and Storage
        cmdline += ["-sysdir", args.sysdir] if args.sysdir else []
        cmdline += ["-system", args.system] if args.system else []
        cmdline += ["-vendor", args.vendor] if args.vendor else []
        cmdline += ["-kernel", args.kernel] if args.kernel else []
        cmdline += ["-ramdisk", args.ramdisk] if args.ramdisk else []
        cmdline += ["-data", args.data] if args.data else []
        cmdline += ["-encryption-key", args.encryption_key] if args.encryption_key else []
        cmdline += ["-cache", args.cache] if args.cache else []
        cmdline += ["-cache-size", str(args.cache_size)] if args.cache_size else []
        cmdline += ["-no-cache"] if args.no_cache else []
        cmdline += ["-datadir", args.datadir] if args.datadir else []
        cmdline += ["-initdata", args.initdata] if args.initdata else []

        # Snapshot Management
        cmdline += ["-snapstorage", args.snapstorage] if args.snapstorage else []
        cmdline += ["-no-snapstorage"] if args.no_snapstorage else []
        cmdline += ["-snapshot", args.snapshot] if args.snapshot else []
        cmdline += ["-no-snapshot"] if args.no_snapshot else []
        cmdline += ["-no-snapshot-save"] if args.no_snapshot_save else []
        cmdline += ["-no-snapshot-load"] if args.no_snapshot_load else []
        cmdline += ["-force-snapshot-load"] if args.force_snapshot_load else []
        cmdline += ["-no-snapshot-update-time"] if args.no_snapshot_update_time else []
        cmdline += ["-snapshot-list"] if args.snapshot_list else []
        cmdline += ["-qcow2-for-userdata"] if args.qcow2_for_userdata else []

        # Display and GPU
        cmdline += ["-no-window"] if args.no_window else []
        cmdline += ["-gpu", args.gpu] if args.gpu else []
        cmdline += ["-no-boot-anim"] if args.no_boot_anim else []
        cmdline += ["-skin", args.skin] if args.skin else []
        cmdline += ["-skindir", args.skindir] if args.skindir else []
        cmdline += ["-no-skin"] if args.no_skin else []
        cmdline += ["-dpi-device", str(args.dpi_device)] if args.dpi_device else []
        cmdline += ["-fixed-scale"] if args.fixed_scale else []
        cmdline += ["-scale", args.scale] if args.scale else []
        cmdline += ["-vsync-rate", str(args.vsync_rate)] if args.vsync_rate else []
        cmdline += ["-qt-hide-window"] if args.qt_hide_window else []
        for display in args.multidisplay:
            cmdline += ["-multidisplay", ",".join(map(str, display))]
        cmdline += ["-no-location-ui"] if args.no_location_ui else []
        cmdline += ["-no-hidpi-scaling"] if args.no_hidpi_scaling else []
        cmdline += ["-no-mouse-reposition"] if args.no_mouse_reposition else []
        for name, file in args.virtualscene_poster.items():
            cmdline += ["-virtualscene-poster", f"{name}={file}"]
        cmdline += ["-guest-angle"] if args.guest_angle else []
        cmdline += ["-window-size", args.window_size] if args.window_size else []
        cmdline += ["-screen", args.screen] if args.screen else []
        cmdline += ["-use-host-vulkan"] if args.use_host_vulkan else []
        cmdline += ["-share-vid"] if args.share_vid else []
        cmdline += ["-hotplug-multi-display"] if args.hotplug_multi_display else []

        # Network Configuration
        cmdline += ["-wifi-client-port", str(args.wifi_client_port)] if args.wifi_client_port else []
        cmdline += ["-wifi-server-port", str(args.wifi_server_port)] if args.wifi_server_port else []
        cmdline += ["-net-tap", args.net_tap] if args.net_tap else []
        cmdline += ["-net-tap-script-up", args.net_tap_script_up] if args.net_tap_script_up else []
        cmdline += ["-net-tap-script-down", args.net_tap_script_down] if args.net_tap_script_down else []
        cmdline += ["-net-socket", args.net_socket] if args.net_socket else []
        cmdline += ["-dns-server", args.dns_server] if args.dns_server else []
        cmdline += ["-http-proxy", args.http_proxy] if args.http_proxy else []
        cmdline += ["-netdelay", args.netdelay] if args.netdelay else []
        cmdline += ["-netspeed", args.netspeed] if args.netspeed else []
        cmdline += ["-port", str(args.port)] if args.port else []
        cmdline += ["-ports", args.ports] if args.ports else []
        cmdline += ["-netfast"] if args.netfast else []
        cmdline += ["-shared-net-id", str(args.shared_net_id)] if args.shared_net_id else []
        cmdline += ["-wifi-tap", args.wifi_tap] if args.wifi_tap else []
        cmdline += ["-wifi-tap-script-up", args.wifi_tap_script_up] if args.wifi_tap_script_up else []
        cmdline += ["-wifi-tap-script-down", args.wifi_tap_script_down] if args.wifi_tap_script_down else []
        cmdline += ["-wifi-socket", args.wifi_socket] if args.wifi_socket else []
        cmdline += ["-vmnet-bridged", args.vmnet_bridged] if args.vmnet_bridged else []
        cmdline += ["-vmnet-shared"] if args.vmnet_shared else []
        cmdline += ["-vmnet-start-address", args.vmnet_start_address] if args.vmnet_start_address else []
        cmdline += ["-vmnet-end-address", args.vmnet_end_address] if args.vmnet_end_address else []
        cmdline += ["-vmnet-subnet-mask", args.vmnet_subnet_mask] if args.vmnet_subnet_mask else []
        cmdline += ["-vmnet-isolated"] if args.vmnet_isolated else []
        cmdline += ["-wifi-user-mode-options", args.wifi_user_mode_options] if args.wifi_user_mode_options else []
        cmdline += (
            ["-network-user-mode-options", args.network_user_mode_options] if args.network_user_mode_options else []
        )
        cmdline += ["-wifi-mac-address", args.wifi_mac_address] if args.wifi_mac_address else []
        cmdline += ["-no-ethernet"] if args.no_ethernet else []

        # Audio Configuration
        cmdline += ["-no-audio"] if args.no_audio else []
        cmdline += ["-audio", args.audio] if args.audio else []
        cmdline += ["-allow-host-audio"] if args.allow_host_audio else []
        cmdline += ["-radio", args.radio] if args.radio else []

        # Camera Configuration
        cmdline += ["-camera-back", args.camera_back] if args.camera_back else []
        cmdline += ["-camera-front", args.camera_front] if args.camera_front else []
        cmdline += ["-legacy-fake-camera"] if args.legacy_fake_camera else []
        cmdline += ["-camera-hq-edge"] if args.camera_hq_edge else []

        # Localization
        cmdline += ["-timezone", args.timezone] if args.timezone else []
        cmdline += ["-change-language", args.change_language] if args.change_language else []
        cmdline += ["-change-country", args.change_country] if args.change_country else []
        cmdline += ["-change-locale", args.change_locale] if args.change_locale else []

        # Security
        cmdline += ["-selinux", args.selinux] if args.selinux else []
        cmdline += ["-skip-adb-auth"] if args.skip_adb_auth else []

        # Hardware Acceleration
        cmdline += ["-accel", args.accel] if args.accel else []
        cmdline += ["-no-accel"] if args.no_accel else []
        cmdline += ["-engine", args.engine] if args.engine else []
        cmdline += ["-ranchu"] if args.ranchu else []
        cmdline += ["-cpu-delay", str(args.cpu_delay)] if args.cpu_delay else []

        # Debugging and Monitoring
        cmdline += ["-verbose"] if args.verbose else []
        cmdline += ["-show-kernel"] if args.show_kernel else []
        cmdline += ["-logcat", args.logcat] if args.logcat else []
        cmdline += ["-logcat-output", args.logcat_output] if args.logcat_output else []
        cmdline += ["-debug", args.debug_tags] if args.debug_tags else []
        cmdline += ["-tcpdump", args.tcpdump] if args.tcpdump else []
        cmdline += ["-detect-image-hang"] if args.detect_image_hang else []
        cmdline += ["-save-path", args.save_path] if args.save_path else []
        cmdline += ["-metrics-to-console"] if args.metrics_to_console else []
        cmdline += ["-metrics-collection"] if args.metrics_collection else []
        cmdline += ["-metrics-to-file", args.metrics_to_file] if args.metrics_to_file else []
        cmdline += ["-no-metrics"] if args.no_metrics else []
        cmdline += ["-perf-stat", args.perf_stat] if args.perf_stat else []
        cmdline += ["-no-nested-warnings"] if args.no_nested_warnings else []
        cmdline += ["-no-direct-adb"] if args.no_direct_adb else []
        cmdline += ["-check-snapshot-loadable", args.check_snapshot_loadable] if args.check_snapshot_loadable else []

        # gRPC Configuration
        cmdline += ["-grpc-port", str(args.grpc_port)] if args.grpc_port else []
        cmdline += ["-grpc-tls-key", args.grpc_tls_key] if args.grpc_tls_key else []
        cmdline += ["-grpc-tls-cert", args.grpc_tls_cert] if args.grpc_tls_cert else []
        cmdline += ["-grpc-tls-ca", args.grpc_tls_ca] if args.grpc_tls_ca else []
        cmdline += ["-grpc-use-token"] if args.grpc_use_token else []
        cmdline += ["-grpc-use-jwt"] if args.grpc_use_jwt else []
        cmdline += ["-grpc-allowlist", args.grpc_allowlist] if args.grpc_allowlist else []
        cmdline += ["-idle-grpc-timeout", str(args.idle_grpc_timeout)] if args.idle_grpc_timeout else []
        cmdline += ["-grpc-ui"] if args.grpc_ui else []

        # Advanced System Configuration
        cmdline += ["-acpi-config", args.acpi_config] if args.acpi_config else []
        for key, value in args.append_userspace_opt.items():
            cmdline += ["-append-userspace-opt", f"{key}={value}"]
        for feature, enabled in args.feature.items():
            cmdline += ["-feature", f"{feature}={'on' if enabled else 'off'}"]
        cmdline += ["-icc-profile", args.icc_profile] if args.icc_profile else []
        cmdline += ["-sim-access-rules-file", args.sim_access_rules_file] if args.sim_access_rules_file else []
        cmdline += ["-phone-number", args.phone_number] if args.phone_number else []
        if args.usb_passthrough:
            cmdline += ["-usb-passthrough"] + list(map(str, args.usb_passthrough))
        cmdline += ["-waterfall", args.waterfall] if args.waterfall else []
        cmdline += ["-restart-when-stalled"] if args.restart_when_stalled else []
        cmdline += ["-wipe-data"] if args.wipe_data else []
        cmdline += ["-delay-adb"] if args.delay_adb else []
        cmdline += ["-quit-after-boot", str(args.quit_after_boot)] if args.quit_after_boot else []
        cmdline += ["-android-serialno", args.android_serialno] if args.android_serialno else []
        cmdline += ["-systemui-renderer", args.systemui_renderer] if args.systemui_renderer else []

        # QEMU Configuration
        if args.qemu_args:
            cmdline += ["-qemu"] + args.qemu_args
        for key, value in args.props.items():
            cmdline += ["-prop", f"{key}={value}"]
        cmdline += ["-adb-path", args.adb_path] if args.adb_path else []

        return cmdline

    @export
    def on(self) -> None:
        if self._process is not None:
            self.logger.warning("Android emulator is already powered on, ignoring request.")
            return

        # Create the emulator command line options
        cmdline = self._make_emulator_command()

        # Prepare environment variables
        env = dict(os.environ)
        env.update(self.parent.emulator.env)

        # Set the ADB server address and port
        env["ANDROID_ADB_SERVER_PORT"] = str(self.parent.adb.port)
        env["ANDROID_ADB_SERVER_ADDRESS"] = self.parent.adb.host

        self.logger.info(f"Starting Android emulator with command: {' '.join(cmdline)}")
        self._process = subprocess.Popen(
            cmdline,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=False,  # Keep as bytes for proper encoding handling
            env=env,
        )

        # Process logs in separate threads
        self._log_thread = threading.Thread(target=self._process_logs, args=(self._process.stdout,), daemon=True)
        self._stderr_thread = threading.Thread(
            target=self._process_logs, args=(self._process.stderr, True), daemon=True
        )
        self._log_thread.start()
        self._stderr_thread.start()

    @export
    def off(self) -> None:  # noqa: C901
        if self._process is not None and self._process.returncode is None:
            # First, attempt to power off emulator using adb command
            try:
                result = subprocess.run(
                    [self.parent.adb.adb_path, "-s", f"emulator-{self.parent.emulator.port}", "emu", "kill"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env={"ANDROID_ADB_SERVER_PORT": str(self.parent.adb.port), **dict(os.environ)},
                )
                # Print output and errors as debug
                for line in result.stdout.splitlines():
                    if line.strip():
                        self.logger.debug(line)
                for line in result.stderr.splitlines():
                    if line.strip():
                        self.logger.debug(line)
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to power off Android emulator: {e}")
            # If the adb command fails, kill the process directly
            except Exception as e:
                self.logger.error(f"Unexpected error while powering off Android emulator: {e}")

            # Wait up to 20 seconds for process to terminate after sending emu kill
            try:
                self._process.wait(timeout=20)
            except TimeoutExpired:
                self.logger.warning("Android emulator did not exit within 20 seconds after 'emu kill' command")
                # Attempt to kill the process directly
                try:
                    self.logger.warning("Attempting to kill Android emulator process directly.")
                    self._process.kill()
                except ProcessLookupError:
                    self.logger.warning("Android emulator process not found, it may have already exited.")

            # Attempt to join the logging threads
            try:
                if self._log_thread is not None:
                    self._log_thread.join(timeout=2)
                if self._stderr_thread is not None:
                    self._stderr_thread.join(timeout=2)
            except TimeoutError:
                self.logger.warning("Log processing threads did not exit cleanly")

            # Clean up process and threads
            self._process = None
            self._log_thread = None
            self._stderr_thread = None
            self.logger.info("Android emulator powered off.")
        else:
            self.logger.warning("Android emulator is already powered off, ignoring request.")

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        return

    def close(self):
        self.off()
