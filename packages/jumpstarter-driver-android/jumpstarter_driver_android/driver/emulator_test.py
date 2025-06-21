import os
import subprocess
from subprocess import TimeoutExpired
from unittest.mock import MagicMock, call, patch

import pytest

from jumpstarter_driver_android.driver.emulator import AndroidEmulator, AndroidEmulatorPower
from jumpstarter_driver_android.driver.options import AdbOptions, EmulatorOptions


@pytest.fixture
# Need to patch the imports in the AndroidDevice class
@patch("jumpstarter_driver_android.driver.device.AdbServer")
@patch("jumpstarter_driver_android.driver.device.Scrcpy")
def android_emulator(scrcpy: MagicMock, adb: MagicMock):
    adb.return_value = MagicMock()
    scrcpy.return_value = MagicMock()
    emulator = AndroidEmulator(
        emulator=EmulatorOptions(emulator_path="/path/to/emulator", avd="test_avd", port=5554),
        adb=AdbOptions(adb_path="/path/to/adb", port=5037),
    )
    return emulator


@pytest.fixture
def emulator_power(android_emulator: AndroidEmulator):
    return AndroidEmulatorPower(parent=android_emulator)


@patch("subprocess.Popen")
@patch("threading.Thread")
def test_emulator_on(_: MagicMock, mock_popen: MagicMock, emulator_power: AndroidEmulatorPower):
    mock_process = MagicMock()
    mock_popen.return_value = mock_process

    emulator_power.on()

    expected_calls = [
        call(
            [
                "/path/to/emulator",
                "-avd",
                "test_avd",
                "-cores",
                "4",
                "-memory",
                "2048",
                "-no-window",
                "-gpu",
                "auto",
                "-scale",
                "1",
                "-netdelay",
                "none",
                "-netspeed",
                "full",
                "-port",
                "5554",
                "-camera-back",
                "emulated",
                "-camera-front",
                "emulated",
                "-accel",
                "auto",
                "-engine",
                "auto",
                "-grpc-use-jwt",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=False,
            env={
                **dict(os.environ),
                **emulator_power.parent.emulator.env,
                "ANDROID_ADB_SERVER_PORT": "5037",
                "ANDROID_ADB_SERVER_ADDRESS": "127.0.0.1",
            },
        )
    ]

    mock_popen.assert_has_calls(expected_calls, any_order=True)


@patch("subprocess.run")
def test_emulator_off_adb_kill(mock_run: MagicMock, emulator_power: AndroidEmulatorPower):
    mock_process = MagicMock()
    mock_process.returncode = None
    emulator_power._process = mock_process
    mock_run.return_value = MagicMock(stdout="Emulator killed", stderr="", returncode=0)

    emulator_power.off()

    # Assert that ADB kill is executed
    mock_run.assert_called_once_with(
        [
            "/path/to/adb",
            "-s",
            "emulator-5554",
            "emu",
            "kill",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "ANDROID_ADB_SERVER_PORT": "5037",
            **dict(os.environ),
        },
    )

    # Verify that the process wait was called
    mock_process.wait.assert_called_once_with(timeout=20)
    mock_process.kill.assert_not_called()

    # Verify that the process and threads are cleaned up
    assert emulator_power._process is None
    assert emulator_power._log_thread is None
    assert emulator_power._stderr_thread is None


@patch("subprocess.run")
def test_emulator_off_timeout(mock_run: MagicMock, emulator_power: AndroidEmulatorPower):
    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.wait = MagicMock(
        side_effect=TimeoutExpired(cmd="/path/to/adb -s emulator-5554 emu kill", timeout=20)
    )  # Simulate timeout
    mock_process.kill = MagicMock()  # Simulate process kill
    emulator_power._process = mock_process

    emulator_power.off()

    # Verify that the process wait was called
    mock_process.wait.assert_called_once_with(timeout=20)

    # Verify that the process kill was called after timeout
    mock_process.kill.assert_called_once()

    # Verify that the process and threads are cleaned up
    assert emulator_power._process is None
    assert emulator_power._log_thread is None
    assert emulator_power._stderr_thread is None


@patch("subprocess.Popen")
@patch("threading.Thread")
@patch("jumpstarter_driver_android.driver.device.AdbServer")
@patch("jumpstarter_driver_android.driver.device.Scrcpy")
def test_emulator_arguments(scrcpy: MagicMock, adb: MagicMock, mock_thread: MagicMock, mock_popen: MagicMock):
    adb.return_value = MagicMock()
    scrcpy.return_value = MagicMock()
    mock_process = MagicMock()
    mock_popen.return_value = mock_process

    emulator_options = EmulatorOptions(
        emulator_path="/path/to/emulator",
        avd="test_avd",
        sysdir="/path/to/sysdir",
        system="/path/to/system.img",
        vendor="/path/to/vendor.img",
        kernel="/path/to/kernel",
        ramdisk="/path/to/ramdisk.img",
        data="/path/to/userdata.img",
        sdcard="/path/to/sdcard.img",
        snapshot="/path/to/snapshot.img",
        avd_arch="x86_64",
        id="test_id",
        cores=4,
        memory=2048,
        encryption_key="/path/to/key",
        cache="/path/to/cache",
        cache_size=1024,
        no_cache=True,
        datadir="/path/to/data",
        initdata="/path/to/initdata",
        snapstorage="/path/to/snapstorage",
        no_snapstorage=True,
        no_snapshot=True,
        no_snapshot_save=True,
        no_snapshot_load=True,
        force_snapshot_load=True,
        no_snapshot_update_time=True,
        snapshot_list=True,
        qcow2_for_userdata=True,
        no_window=True,
        gpu="host",
        no_boot_anim=True,
        skin="pixel_2",
        skindir="/path/to/skins",
        no_skin=True,
        dpi_device=420,
        fixed_scale=True,
        scale="1.0",
        vsync_rate=60,
        qt_hide_window=True,
        multidisplay=[(0, 0, 1080, 1920, 0)],
        no_location_ui=True,
        no_hidpi_scaling=True,
        no_mouse_reposition=True,
        virtualscene_poster={"name": "/path/to/poster.jpg"},
        guest_angle=True,
        window_size="1080x1920",
        screen="touch",
        use_host_vulkan=True,
        share_vid=True,
        hotplug_multi_display=True,
        wifi_client_port=5555,
        wifi_server_port=5556,
        net_tap="tap0",
        net_tap_script_up="/path/to/up.sh",
        net_tap_script_down="/path/to/down.sh",
        net_socket="socket0",
        dns_server="8.8.8.8",
        http_proxy="http://proxy:8080",
        netdelay="none",
        netspeed="full",
        port=5554,
        ports="5554,5555",
        netfast=True,
        shared_net_id=1,
        wifi_tap="wifi0",
        wifi_tap_script_up="/path/to/wifi_up.sh",
        wifi_tap_script_down="/path/to/wifi_down.sh",
        wifi_socket="wifi_socket",
        vmnet_bridged="en0",
        vmnet_shared=True,
        vmnet_start_address="192.168.1.1",
        vmnet_end_address="192.168.1.254",
        vmnet_subnet_mask="255.255.255.0",
        vmnet_isolated=True,
        wifi_user_mode_options="option1=value1",
        network_user_mode_options="option2=value2",
        wifi_mac_address="00:11:22:33:44:55",
        no_ethernet=True,
        no_audio=True,
        audio="host",
        allow_host_audio=True,
        radio="modem",
        camera_back="webcam0",
        camera_front="emulated",
        legacy_fake_camera=True,
        camera_hq_edge=True,
        timezone="America/New_York",
        change_language="en",
        change_country="US",
        change_locale="en_US",
        selinux="permissive",
        skip_adb_auth=True,
        accel="auto",
        no_accel=True,
        engine="auto",
        ranchu=True,
        cpu_delay=100,
        verbose=True,
        show_kernel=True,
        logcat="*:V",
        logcat_output="/path/to/logcat.txt",
        debug_tags="all",
        tcpdump="/path/to/capture.pcap",
        detect_image_hang=True,
        save_path="/path/to/save",
        metrics_to_console=True,
        metrics_collection=True,
        metrics_to_file="/path/to/metrics.txt",
        no_metrics=True,
        perf_stat="cpu",
        no_nested_warnings=True,
        no_direct_adb=True,
        check_snapshot_loadable="/path/to/snapshot",
        grpc_port=8554,
        grpc_tls_key="/path/to/key.pem",
        grpc_tls_cert="/path/to/cert.pem",
        grpc_tls_ca="/path/to/ca.pem",
        grpc_use_token=True,
        grpc_use_jwt=True,
        grpc_allowlist="allowlist.txt",
        idle_grpc_timeout=60,
        grpc_ui=True,
        acpi_config="/path/to/acpi.ini",
        append_userspace_opt={"opt1": "value1"},
        feature={"feature1": True},
        icc_profile="/path/to/icc.profile",
        sim_access_rules_file="/path/to/sim.rules",
        phone_number="+1234567890",
        usb_passthrough=[1, 2, 3, 4],
        waterfall="/path/to/waterfall",
        restart_when_stalled=True,
        wipe_data=True,
        delay_adb=True,
        quit_after_boot=30,
        android_serialno="emulator-5554",
        systemui_renderer="skia",
        qemu_args=["-enable-kvm"],
        props={"prop1": "value1"},
        adb_path="/path/to/adb",
    )
    emulator = AndroidEmulator(emulator=emulator_options)

    # Call the on method to trigger the command construction
    emulator.children["power"].on()  # type: ignore

    # Verify the command line arguments
    expected_args = [
        "/path/to/emulator",
        "-avd",
        "test_avd",
        "-avd-arch",
        "x86_64",
        "-id",
        "test_id",
        "-cores",
        "4",
        "-memory",
        "2048",
        "-sysdir",
        "/path/to/sysdir",
        "-system",
        "/path/to/system.img",
        "-vendor",
        "/path/to/vendor.img",
        "-kernel",
        "/path/to/kernel",
        "-ramdisk",
        "/path/to/ramdisk.img",
        "-data",
        "/path/to/userdata.img",
        "-encryption-key",
        "/path/to/key",
        "-cache",
        "/path/to/cache",
        "-cache-size",
        "1024",
        "-no-cache",
        "-datadir",
        "/path/to/data",
        "-initdata",
        "/path/to/initdata",
        "-snapstorage",
        "/path/to/snapstorage",
        "-no-snapstorage",
        "-snapshot",
        "/path/to/snapshot.img",
        "-no-snapshot",
        "-no-snapshot-save",
        "-no-snapshot-load",
        "-force-snapshot-load",
        "-no-snapshot-update-time",
        "-snapshot-list",
        "-qcow2-for-userdata",
        "-no-window",
        "-gpu",
        "host",
        "-no-boot-anim",
        "-skin",
        "pixel_2",
        "-skindir",
        "/path/to/skins",
        "-no-skin",
        "-dpi-device",
        "420",
        "-fixed-scale",
        "-scale",
        "1.0",
        "-vsync-rate",
        "60",
        "-qt-hide-window",
        "-multidisplay",
        "0,0,1080,1920,0",
        "-no-location-ui",
        "-no-hidpi-scaling",
        "-no-mouse-reposition",
        "-virtualscene-poster",
        "name=/path/to/poster.jpg",
        "-guest-angle",
        "-window-size",
        "1080x1920",
        "-screen",
        "touch",
        "-use-host-vulkan",
        "-share-vid",
        "-hotplug-multi-display",
        "-wifi-client-port",
        "5555",
        "-wifi-server-port",
        "5556",
        "-net-tap",
        "tap0",
        "-net-tap-script-up",
        "/path/to/up.sh",
        "-net-tap-script-down",
        "/path/to/down.sh",
        "-net-socket",
        "socket0",
        "-dns-server",
        "8.8.8.8",
        "-http-proxy",
        "http://proxy:8080",
        "-netdelay",
        "none",
        "-netspeed",
        "full",
        "-port",
        "5554",
        "-ports",
        "5554,5555",
        "-netfast",
        "-shared-net-id",
        "1",
        "-wifi-tap",
        "wifi0",
        "-wifi-tap-script-up",
        "/path/to/wifi_up.sh",
        "-wifi-tap-script-down",
        "/path/to/wifi_down.sh",
        "-wifi-socket",
        "wifi_socket",
        "-vmnet-bridged",
        "en0",
        "-vmnet-shared",
        "-vmnet-start-address",
        "192.168.1.1",
        "-vmnet-end-address",
        "192.168.1.254",
        "-vmnet-subnet-mask",
        "255.255.255.0",
        "-vmnet-isolated",
        "-wifi-user-mode-options",
        "option1=value1",
        "-network-user-mode-options",
        "option2=value2",
        "-wifi-mac-address",
        "00:11:22:33:44:55",
        "-no-ethernet",
        "-no-audio",
        "-audio",
        "host",
        "-allow-host-audio",
        "-radio",
        "modem",
        "-camera-back",
        "webcam0",
        "-camera-front",
        "emulated",
        "-legacy-fake-camera",
        "-camera-hq-edge",
        "-timezone",
        "America/New_York",
        "-change-language",
        "en",
        "-change-country",
        "US",
        "-change-locale",
        "en_US",
        "-selinux",
        "permissive",
        "-skip-adb-auth",
        "-accel",
        "auto",
        "-no-accel",
        "-engine",
        "auto",
        "-ranchu",
        "-cpu-delay",
        "100",
        "-verbose",
        "-show-kernel",
        "-logcat",
        "*:V",
        "-logcat-output",
        "/path/to/logcat.txt",
        "-debug",
        "all",
        "-tcpdump",
        "/path/to/capture.pcap",
        "-detect-image-hang",
        "-save-path",
        "/path/to/save",
        "-metrics-to-console",
        "-metrics-collection",
        "-metrics-to-file",
        "/path/to/metrics.txt",
        "-no-metrics",
        "-perf-stat",
        "cpu",
        "-no-nested-warnings",
        "-no-direct-adb",
        "-check-snapshot-loadable",
        "/path/to/snapshot",
        "-grpc-port",
        "8554",
        "-grpc-tls-key",
        "/path/to/key.pem",
        "-grpc-tls-cert",
        "/path/to/cert.pem",
        "-grpc-tls-ca",
        "/path/to/ca.pem",
        "-grpc-use-token",
        "-grpc-use-jwt",
        "-grpc-allowlist",
        "allowlist.txt",
        "-idle-grpc-timeout",
        "60",
        "-grpc-ui",
        "-acpi-config",
        "/path/to/acpi.ini",
        "-append-userspace-opt",
        "opt1=value1",
        "-feature",
        "feature1=on",
        "-icc-profile",
        "/path/to/icc.profile",
        "-sim-access-rules-file",
        "/path/to/sim.rules",
        "-phone-number",
        "+1234567890",
        "-usb-passthrough",
        "1",
        "2",
        "3",
        "4",
        "-waterfall",
        "/path/to/waterfall",
        "-restart-when-stalled",
        "-wipe-data",
        "-delay-adb",
        "-quit-after-boot",
        "30",
        "-android-serialno",
        "emulator-5554",
        "-systemui-renderer",
        "skia",
        "-qemu",
        "-enable-kvm",
        "-prop",
        "prop1=value1",
        "-adb-path",
        "/path/to/adb",
    ]

    mock_popen.assert_called_with(
        expected_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=False,
        env={
            **dict(os.environ),
            **emulator_options.env,
            "ANDROID_ADB_SERVER_PORT": "5037",
            "ANDROID_ADB_SERVER_ADDRESS": "127.0.0.1",
        },
    )
