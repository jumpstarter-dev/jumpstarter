"""Act 1 — a plain Jumpstarter pytest against a QEMU virtual DUT.

Nothing here is new. It is the same `JumpstarterTest` shape a Jumpstarter user has always
written: a class-scoped `client` acquired through the controller (by label selector), driver
calls like `client.dut.power.on()`, and a serial console driven with pexpect. What changed is
entirely underneath — the lease and the whole transport now run through the Rust core and a Rust
controller. The test can't tell, and neither can the audience.

Run it under a controller lease:

    # host the QEMU exporter (spawns qemu-system-aarch64 on this Mac)
    jmp run --exporter demo-qemu

    # in another shell, with the demo venv active:
    jmp shell --client demo-client --selector example.com/dut=qemu -- \
        pytest -s examples/rust-core-demo/act1-python-qemu/test_qemu_boot.py

Set DEBUG_CONSOLE=1 to mirror the guest serial output to stdout.
"""

import os
import sys

from jumpstarter_driver_network.adapters import PexpectAdapter
from jumpstarter_testing.pytest import JumpstarterTest

# cloud-init creates this login on first boot (from the driver's generated CIDATA drive); it
# matches the exporter config's username/password.
USERNAME = "jumpstarter"
PASSWORD = "password"

# QEMU boots under TCG (software emulation) on macOS, so give first boot + cloud-init room.
BOOT_TIMEOUT = 300


class TestQemuDut(JumpstarterTest):
    selector = "example.com/dut=qemu"

    def test_boot_and_login(self, client):
        # Power on the virtual DUT. QemuPower.on() launches qemu-system-aarch64, wires up the
        # serial PTY, and issues a system_reset — so the console must be opened AFTER this returns
        # (the PTY does not exist until the VM is running).
        client.dut.power.on()
        try:
            with PexpectAdapter(client=client.dut.console) as console:
                if os.environ.get("DEBUG_CONSOLE") == "1":
                    console.logfile_read = sys.stdout.buffer

                # Wait for the guest to reach a serial login prompt, then log in.
                console.expect("login:", timeout=BOOT_TIMEOUT)
                console.sendline(USERNAME)
                console.expect("[Pp]assword:", timeout=30)
                console.sendline(PASSWORD)

                # Prove we have an interactive shell: run a command and read its output back.
                console.expect(r"[$#] ", timeout=60)
                console.sendline("uname -a")
                console.expect("Linux", timeout=30)
                console.sendline("echo jumpstarter-was-here")
                console.expect("jumpstarter-was-here", timeout=30)
        finally:
            # Always power the DUT back down so the lease releases cleanly.
            client.dut.power.off()
