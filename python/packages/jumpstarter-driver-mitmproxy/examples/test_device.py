"""
Example HiL tests for DUT connected services.

Demonstrates how to use the mitmproxy Jumpstarter driver to mock
backend APIs and verify DUT behavior under different conditions.

Run with:
    jmp start --exporter my-bench -- pytest tests/ -v
"""

from __future__ import annotations

import time


class TestDeviceStatusDisplay:
    """Verify the DUT displays status information correctly."""

    def test_shows_device_info(self, client, proxy, mock_device_status):
        """DUT should display device status from the API."""
        # Interact with DUT to navigate to the status screen.
        # Replace with your device-specific interaction (serial, adb, etc.)
        serial = client.serial
        serial.write(b"open-status-screen\n")
        time.sleep(5)

        # Capture screenshot for verification
        screenshot = client.video.snapshot()
        assert screenshot is not None
        # TODO: Use jumpstarter-imagehash or OCR to verify display content

    def test_handles_backend_503(self, client, proxy, mock_backend_down):
        """DUT should show a graceful error when backend is down."""
        serial = client.serial
        serial.write(b"open-status-screen\n")
        time.sleep(5)

        screenshot = client.video.snapshot()
        assert screenshot is not None
        # Verify retry/error UI is shown instead of a crash

    def test_handles_timeout(self, client, proxy, mock_slow_backend):
        """DUT should handle gateway timeouts gracefully."""
        serial = client.serial
        serial.write(b"open-status-screen\n")
        time.sleep(10)  # Longer wait for timeout handling

        screenshot = client.video.snapshot()
        assert screenshot is not None

    def test_handles_auth_expiry(self, client, proxy, mock_auth_expired):
        """DUT should prompt re-authentication on 401."""
        serial = client.serial
        serial.write(b"open-status-screen\n")
        time.sleep(5)

        screenshot = client.video.snapshot()
        assert screenshot is not None
        # Verify login/re-auth prompt is shown


class TestFirmwareUpdate:
    """Verify the firmware update flow with mocked backend."""

    def test_update_notification_shown(
        self, client, proxy, mock_update_available,
    ):
        """DUT should notify user when an update is available."""
        serial = client.serial
        serial.write(b"check-for-update\n")
        time.sleep(10)

        screenshot = client.video.snapshot()
        assert screenshot is not None
        # Verify update notification dialog is visible

    def test_no_update_message(
        self, client, proxy, mock_up_to_date,
    ):
        """DUT should show 'up to date' when no update exists."""
        serial = client.serial
        serial.write(b"check-for-update\n")
        time.sleep(10)

        screenshot = client.video.snapshot()
        assert screenshot is not None


class TestDynamicMocking:
    """Demonstrate runtime mock configuration within a test."""

    def test_mock_then_unmock(self, client, proxy):
        """Show how to set and remove mocks within a single test."""
        # Start with a healthy response
        proxy.set_mock(
            "GET", "/api/v1/status",
            body={"status": "active", "battery_pct": 85},
        )

        serial = client.serial
        serial.write(b"open-status-screen\n")
        time.sleep(5)
        healthy_screenshot = client.video.snapshot()

        # Now simulate a failure
        proxy.set_mock(
            "GET", "/api/v1/status",
            status=500,
            body={"error": "Internal Server Error"},
        )

        # Trigger a refresh
        serial.write(b"refresh\n")
        time.sleep(5)
        error_screenshot = client.video.snapshot()

        # Remove the mock to restore passthrough
        proxy.remove_mock("GET", "/api/v1/status")

        assert healthy_screenshot is not None
        assert error_screenshot is not None

    def test_load_full_scenario(self, client, proxy):
        """Load a complete mock scenario from a JSON file."""
        with proxy.mock_scenario("happy-path.json"):
            serial = client.serial
            serial.write(b"open-dashboard\n")
            time.sleep(5)
            screenshot = client.video.snapshot()
            assert screenshot is not None


class TestTrafficRecording:
    """Demonstrate recording and replaying DUT traffic."""

    def test_record_golden_session(self, client, proxy):
        """Record a session for later replay in CI."""
        with proxy.recording() as p:
            serial = client.serial

            # Walk through a standard user flow
            serial.write(b"open-status-screen\n")
            time.sleep(5)
            serial.write(b"go-back\n")
            time.sleep(2)

        # Verify recording was saved
        files = p.list_flow_files()
        assert len(files) > 0
        print(f"Recorded to: {files[-1]['name']}")


class TestWebUIAccess:
    """Verify the mitmweb UI is accessible for debugging."""

    def test_web_ui_url_available(self, proxy):
        """When started with web_ui=True, URL should be available."""
        url = proxy.web_ui_url
        assert url is not None
        assert ":8081" in url or ":18081" in url
        print(f"\n>>> Open {url} in your browser to inspect traffic")
