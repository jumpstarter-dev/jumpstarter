"""Acceptance tests for the board the demo just flashed and deployed to.

These run on the client, against the lease held by the surrounding
`jmp shell`: the `client` fixture from JumpstarterTest picks up
JUMPSTARTER_HOST from the environment. The same file works standalone with
`pytest demo/tests` when a client config and a matching board are available.

The suite is shaped like something a team would actually gate a build on:

* the image that booted is the one that was flashed, and it booted cleanly
* cold boot fits inside a time budget, which is a hard requirement on
  automotive targets
* a deployed workload runs, and keeps working while the board is saturated
* the board comes back from having its power cut, still within budget

The last two are the ones an ordinary test framework cannot do, because they
need control over the hardware rather than just a shell on it.
"""

import os
import re
import time

import pytest
from jumpstarter_driver_ssh.client import SSHCommandRunOptions
from jumpstarter_testing.pytest import JumpstarterTest


def _env_int(name, fallback):
    value = os.environ.get(name, "").strip()
    return int(value) if value else fallback


SELECTOR = os.environ.get("DEMO_SELECTOR", "board-type=j784s4evm")
OS_NAME = os.environ.get("DEMO_OS_NAME", "Automotive Stream Distribution")

# The disk image the demo flashed, e.g. oci://quay.io/org/autosd-demo-disk:v1.
# bootc reports the container image the disk was built from, which shares the
# repository path but not the "-disk" suffix or the oci:// scheme.
FLASHED_IMAGE = os.environ.get("DEMO_IMAGE", "")

#: Seconds a cold boot may take, from power on to userspace being up.
BOOT_BUDGET = _env_int("DEMO_BOOT_BUDGET", 30)
#: Seconds to saturate the board for.
SOAK_SECONDS = _env_int("DEMO_SOAK_SECONDS", 20)
#: Memory each stress-ng vm worker touches.
SOAK_VM_BYTES = os.environ.get("DEMO_SOAK_VM_BYTES", "512M")

# The image keeps /etc on a transient ostree overlay that is discarded on
# every boot, so a deployed workload has to live under /var to survive one.
STATE = "/var/lib/jumpstarter-demo"
WORKLOAD = f"{STATE}/run.sh"
LAST_RUN = f"{STATE}/last-run"
BOOT_ID = "/proc/sys/kernel/random/boot_id"

_DURATION_UNITS = {"h": 3600.0, "min": 60.0, "s": 1.0, "ms": 0.001}


def parse_duration(text):
    """Parse a systemd duration such as '14.040s' or '1min 2.340s'."""
    total = 0.0
    for value, unit in re.findall(r"([\d.]+)(h|min|ms|s)", text):
        total += float(value) * _DURATION_UNITS[unit]
    return total


def sh(client, command, *, check=True):
    """Run a shell command on the board and return its stdout."""
    result = client.ssh.run(SSHCommandRunOptions(), ["--", command])
    if check and result.return_code != 0:
        raise AssertionError(
            f"`{command}` failed with {result.return_code}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return (result.stdout or "").strip()


def boot_time(client):
    """Total cold boot time in seconds, per systemd."""
    report = sh(client, "systemd-analyze time")
    line = next(
        (line for line in report.splitlines() if "Startup finished" in line), ""
    )
    assert line, f"could not read a boot time from:\n{report}"
    return parse_duration(line.split("=")[-1]), line


def failed_units(client):
    return sh(
        client, "systemctl list-units --state=failed --no-legend --plain", check=False
    )


def wait_for_ssh(client, timeout=300, interval=5, stable=2):
    """Block until the board answers over ssh with usable output.

    Just after a reboot the first connections can succeed while returning
    nothing at all - the command exits 0 but its stdout never arrives - so
    wait for the output to round trip, several times in a row, rather than
    settling for an exit code.
    """
    deadline = time.monotonic() + timeout
    good = 0
    last = None
    while time.monotonic() < deadline:
        try:
            if sh(client, "echo ready") == "ready":
                good += 1
                if good >= stable:
                    return
            else:
                good = 0
                last = "connected but stdout was empty"
        except Exception as err:  # noqa: BLE001 - anything here means "not up yet"
            good = 0
            last = err
        time.sleep(interval)
    raise AssertionError(f"board did not come back within {timeout}s: {last}")


class TestFlashedBoard(JumpstarterTest):
    selector = SELECTOR

    def test_booted_the_image_we_flashed(self, client):
        """The OS that came up is the one the demo just wrote to the disk."""
        assert OS_NAME in sh(client, "cat /etc/os-release")

        if not FLASHED_IMAGE:
            pytest.skip("DEMO_IMAGE is not set, cannot compare the booted image")

        booted = sh(
            client, "bootc status --format json | grep -o '\"image\":\"[^\"]*\"' | head -1"
        )
        repository = FLASHED_IMAGE.removeprefix("oci://").split(":")[0].removesuffix("-disk")
        assert repository in booted, f"booted {booted}, expected something from {repository}"

    def test_system_came_up_clean(self, client):
        """No systemd units failed while booting the fresh image."""
        assert failed_units(client) == "", f"failed units after boot:\n{failed_units(client)}"

    def test_cold_boot_is_within_budget(self, client):
        """Cold boot fits the time budget.

        Automotive targets have hard wake-up requirements, so this is the
        kind of number a build gets gated on rather than merely reported.
        """
        seconds, line = boot_time(client)
        slowest = sh(client, "systemd-analyze blame --no-pager | head -3", check=False)
        assert seconds <= BOOT_BUDGET, (
            f"cold boot took {seconds:.1f}s, budget is {BOOT_BUDGET}s\n"
            f"{line}\nslowest units:\n{slowest}"
        )

    def test_workload_was_deployed(self, client):
        """`j mount` really put the workload onto the board's filesystem."""
        assert sh(client, f"test -x {WORKLOAD} && echo yes") == "yes"

    def test_workload_runs(self, client):
        """The deployed workload executes and records the boot it ran under."""
        assert "workload ran" in sh(client, WORKLOAD)
        assert sh(client, f"cat {BOOT_ID}") in sh(client, f"cat {LAST_RUN}")

    def test_stays_healthy_under_load(self, client):
        """Saturate every core and a few GB of memory, then check the damage.

        Idle boards pass almost anything. This is where marginal power,
        cooling or memory actually shows up.
        """
        if not sh(client, "command -v stress-ng || true", check=False):
            pytest.skip("stress-ng is not installed on the image")

        cpus = sh(client, "nproc")
        report = sh(
            client,
            f"stress-ng --cpu {cpus} --vm 2 --vm-bytes {SOAK_VM_BYTES}"
            f" --timeout {SOAK_SECONDS}s --metrics-brief 2>&1",
        )
        assert "failed: 0" in report, f"stress-ng reported failures:\n{report}"
        assert "successful run completed" in report, f"soak did not finish:\n{report}"

        # The board has to be healthy afterwards, not merely survive.
        assert failed_units(client) == "", f"units failed under load:\n{failed_units(client)}"
        killed = sh(
            client,
            "journalctl -k --since '-10 min' | grep -ci 'out of memory\\|oom-kill' || true",
            check=False,
        )
        assert killed == "0", f"the kernel OOM killed {killed} time(s) under load"
        assert "workload ran" in sh(client, WORKLOAD), "workload broke after the soak"

    def test_survives_a_power_cycle(self, client):
        """Cut the power, and the board comes back intact and on time.

        This is the part an ordinary test framework cannot do: the board is
        physically power cycled from inside the test. It also pins down a
        real property of this image, that /var persists across a boot while
        the transient /etc overlay does not.
        """
        before = sh(client, f"cat {BOOT_ID}")

        # power.cycle() cuts the power, it does not shut the board down, so
        # anything still in the page cache is lost. The workload arrived over
        # sshfs and has not necessarily reached the disk yet.
        sh(client, "sync")

        client.power.cycle()
        wait_for_ssh(client)

        after = sh(client, f"cat {BOOT_ID}")
        assert after != before, "board reports the same boot, it never rebooted"

        assert sh(client, f"test -x {WORKLOAD} && echo yes") == "yes"
        assert "workload ran" in sh(client, WORKLOAD)
        assert after in sh(client, f"cat {LAST_RUN}")

        seconds, line = boot_time(client)
        assert seconds <= BOOT_BUDGET, f"boot after power cut took {seconds:.1f}s\n{line}"
        assert failed_units(client) == "", "units failed on the boot after the power cut"
