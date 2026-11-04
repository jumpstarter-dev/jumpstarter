"""Drive the built-in Settings app on an Android emulator through Appium.

Appium runs on the exporter, next to the emulator; the tests reach it through
the Jumpstarter lease with Appium-Python-Client. No APK is needed.
"""

import pytest
from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from jumpstarter_testing.pytest import JumpstarterTest

SETTINGS = "com.android.settings"
# FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_CLEAR_TASK: a fresh Settings task.
NEW_TASK = "0x10008000"


def scroll_to(driver, selector):
    """Scroll the Settings list until an element matching a UiSelector is on screen."""
    return driver.find_element(
        AppiumBy.ANDROID_UIAUTOMATOR,
        f"new UiScrollable(new UiSelector().scrollable(true)).scrollIntoView({selector})",
    )


class TestSettings(JumpstarterTest):
    # Used only outside a `jmp shell`, to lease an exporter with this label.
    selector = "example=appium-android-emulator"

    @pytest.fixture(scope="class")
    @classmethod
    def appium(cls, client):
        """Boot the emulator and yield its Appium endpoint."""
        client.android.power.on()
        try:
            with client.android.appium.serve() as target:
                yield target
        finally:
            client.android.power.off()

    @pytest.fixture(scope="class")
    @classmethod
    def session(cls, appium):
        """One Appium session for the class."""
        # The target fixes platformName, automationName and the emulator's udid.
        options = UiAutomator2Options().load_capabilities(appium.capabilities())
        session = webdriver.Remote(appium.url, options=options, client_config=appium.client_config())
        yield session
        session.quit()

    @pytest.fixture
    def settings(self, session):
        """Start each test from the top of Settings."""
        # Start Settings by its action in a new task, which lands on its home
        # screen even when a previous test left it deeper in.
        session.execute_script(
            "mobile: startActivity", {"action": "android.settings.SETTINGS", "flags": NEW_TASK, "wait": True}
        )
        return session

    def test_settings_is_in_front(self, settings):
        assert settings.current_package == SETTINGS

    def test_about_shows_the_android_version(self, settings):
        # "About emulated device" on the emulator, "About phone" on most phones.
        scroll_to(settings, 'new UiSelector().textStartsWith("About")').click()
        scroll_to(settings, 'new UiSelector().text("Android version")')
        version = settings.find_element(
            AppiumBy.XPATH, '//*[@text="Android version"]/following-sibling::*[@resource-id="android:id/summary"]'
        )
        assert version.text == settings.capabilities["platformVersion"]

    def test_screenshot(self, settings, tmp_path):
        png = settings.get_screenshot_as_png()
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "settings.png").write_bytes(png)
