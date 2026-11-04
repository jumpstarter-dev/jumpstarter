"""Drive the built-in Settings app on an iOS Simulator through Appium.

Appium runs on the exporter, next to the simulator; the tests reach it through
the Jumpstarter lease with Appium-Python-Client. No app build is needed.
"""

import pytest
from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.applicationstate import ApplicationState
from appium.webdriver.common.appiumby import AppiumBy
from jumpstarter_testing.pytest import JumpstarterTest

SETTINGS = "com.apple.Preferences"


class TestSettings(JumpstarterTest):
    # Used only outside a `jmp shell`, to lease an exporter with this label.
    selector = "example=appium-ios-simulator"

    @pytest.fixture(scope="class")
    @classmethod
    def appium(cls, client):
        """Boot the simulator and yield its Appium endpoint."""
        client.simulator.power.on()
        try:
            with client.simulator.appium.serve() as target:
                yield target
        finally:
            client.simulator.power.off()

    @pytest.fixture(scope="class")
    @classmethod
    def session(cls, appium):
        """One Appium session for the class, opened on Settings."""
        # The target fixes platformName, automationName, the simulator's udid
        # and its WebDriverAgent.
        options = XCUITestOptions().load_capabilities(appium.capabilities())
        options.bundle_id = SETTINGS
        session = webdriver.Remote(appium.url, options=options, client_config=appium.client_config())
        yield session
        session.quit()

    @pytest.fixture
    def settings(self, session):
        """Start each test from the top of Settings."""
        session.terminate_app(SETTINGS)
        session.activate_app(SETTINGS)
        return session

    def test_settings_is_in_front(self, settings):
        assert settings.query_app_state(SETTINGS) == ApplicationState.RUNNING_IN_FOREGROUND

    def test_about_shows_the_ios_version(self, settings):
        settings.find_element(AppiumBy.ACCESSIBILITY_ID, "General").click()
        settings.find_element(AppiumBy.ACCESSIBILITY_ID, "About").click()
        # The row's identifier is stable; its label reads "iOS Version, 27.0".
        row = settings.find_element(AppiumBy.ACCESSIBILITY_ID, "SW_VERSION_SPECIFIER")
        assert row.get_attribute("label").endswith(f", {settings.capabilities['platformVersion']}")

    def test_screenshot(self, settings, tmp_path):
        png = settings.get_screenshot_as_png()
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "settings.png").write_bytes(png)
