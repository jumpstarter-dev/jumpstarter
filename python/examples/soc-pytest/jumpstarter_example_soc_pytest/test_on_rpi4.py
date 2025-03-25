import logging
import os
import sys
import time

import opendal
import pexpect
import pytest
from jumpstarter_driver_network.adapters import PexpectAdapter
from jumpstarter_imagehash import ImageHash
from jumpstarter_testing.pytest import JumpstarterTest

log = logging.getLogger(__file__)


class TestResource(JumpstarterTest):
    selector = "board=rpi4"

    @pytest.fixture()
    def console(self, client):
        with PexpectAdapter(client=client.dutlink.console) as console:
            if os.environ.get("DEBUG_CONSOLE") == "1":
                console.logfile_read = sys.stdout.buffer
            yield console

    @pytest.fixture()
    def video(self, client):
        return ImageHash(client.video)

    @pytest.fixture()
    def shell(self, client, console):
        client.dutlink.power.off()
        time.sleep(1)
        client.dutlink.power.on()
        yield _wait_and_login(console, "root", "changeme", "@rpitest:~#")
        _power_off(client, console)

    def test_setup_device(self, client, console):
        client.dutlink.power.off()
        log.info("Setting up device")
        try:
            client.dutlink.storage.write_local_file("image/images/latest.raw")
        except opendal.exceptions.NotFound:
            pytest.exit(
                "No image found, please enter the image directory and run `make`, more details in the README.md"
            )
            return
        client.dutlink.storage.dut()
        client.dutlink.power.on()
        console.logfile_read = sys.stdout.buffer
        # first boot on raspbian will take some time, we wait for the login
        _wait_and_login(console, "root", "changeme", "@rpitest:~#")
        # then power off the device
        _power_off(client, console)

    def test_tpm2_device(self, shell):
        shell.logfile_read = sys.stdout.buffer

        lines = [
            "apt-get install -y tpm2-tools",
            "tpm2_createprimary -C e -c primary.ctx",
            "tpm2_create -G rsa -u key.pub -r key.priv -C primary.ctx",
            "tpm2_load -C primary.ctx -u key.pub -r key.priv -c key.ctx",
            "echo my message > message.dat",
            "tpm2_sign -c key.ctx -g sha256 -o sig.rssa message.dat",
            "tpm2_verifysignature -c key.ctx -g sha256 -s sig.rssa -m message.dat",
        ]

        for line in lines:
            log.info(f"Running command: {line}")
            shell.sendline(line)
            shell.expect("@rpitest:~#", timeout=200)

        shell.sendline("echo result: $?")
        shell.expect(r"result: \d.", timeout=200)
        assert shell.after.decode().strip() == "result: 0"

    def test_power_off_camera(self, client):
        client.dutlink.power.off()
        client.camera.snapshot().save("camera_off.jpeg")

    def test_power_on_camera(self, client):
        client.dutlink.power.on()
        time.sleep(1)
        client.camera.snapshot().save("camera_on.jpeg")
        client.dutlink.power.off()

    def test_power_on_hdmi(self, client, video):
        # check all the image snapshots through the rpi4 boot process
        client.dutlink.power.on()
        time.sleep(1)
        video.assert_snapshot("test_booting_empty_ok.jpeg")
        time.sleep(6)
        video.assert_snapshot("test_booting_rainbow_ok.jpeg")
        time.sleep(4)
        video.assert_snapshot("test_booting_raspberries_ok.jpeg")
        client.dutlink.power.off()

    def test_login_console_hdmi(self, shell, video):
        video.assert_snapshot("test_booted_ok.jpeg")


def _power_off(client, console):
    log.info("Attempting a soft power off")
    try:
        console.sendline("poweroff")
        console.expect("Power down.")
    except pexpect.TIMEOUT:
        log.error("Timeout waiting for power down, continuing with hard power off")
    finally:
        client.dutlink.power.off()


def _wait_and_login(pexpect_console, username, password, prompt, timeout=240):
    log.info("Waiting for login prompt")
    pexpect_console.expect("login:", timeout=timeout)
    pexpect_console.sendline(username)
    pexpect_console.expect("Password:")
    pexpect_console.sendline(password)
    pexpect_console.expect(prompt, timeout=60)
    log.info("Logged in")
    return pexpect_console
