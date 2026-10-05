import json
import os
import tarfile

import pytest
from jumpstarter_driver_power.driver import MockPower

from .conftest import wait_for
from .driver import FastbootFlasher
from .jobs import JobState
from jumpstarter.client.flasher import FlashPhase, FlashStatus
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve
from jumpstarter.driver import export

MANIFEST = """
apiVersion: jumpstarter.dev/v1alpha1
kind: FlashManifest
metadata: {name: test}
spec:
  requires:
    product: testdev
    battery-soc-ok: {equals: "yes", optional: true}
  fastboot:
    slot: inactive
    finally: continue
  steps:
    - name: Bootloader
      critical: true
      fastboot:
        flash:
          - {partition: abl, file: abl.elf}
    - fastboot:
        flash:
          - {partition: boot, file: boot.img}
          - {partition: vbmeta, file: vbmeta.img}
    - fastboot: {reboot: fastboot}
    - fastboot:
        flash:
          - {partition: system, file: system.img}
    - when: wipe
      fastboot: {erase: userdata}
    - fastboot: {reboot: bootloader}
    - fastboot: {set_active: inactive}
"""


def make_driver(fake, tmp_path, **config):
    defaults = {
        "fastboot_path": fake.binary,
        "state_dir": str(tmp_path / "state"),
        "free_space_reserve_bytes": 0,
        "reenumerate_timeout": 5,
        "stall_timeout": 3,
        "command_timeout": 5,
        "step_retries": 2,
        "mode_switch_settle": 0.1,
    }
    if "serial" not in config:
        config.setdefault("usb_port", "1-2")  # bench port, without the usb: prefix
    return FastbootFlasher(**(defaults | config))


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    for name, size in (("abl.elf", 4096), ("boot.img", 8192), ("vbmeta.img", 1024), ("system.img", 16384)):
        (root / name).write_bytes(os.urandom(size))
    (root / "manifest.yaml").write_text(MANIFEST)
    return root


def finish(client, job_id, timeout=60):
    return wait_for(lambda: (info := client.status(job_id))["state"] in
                    ("succeeded", "failed_clean", "failed_partial", "interrupted", "cancelled") and info, timeout)


def test_flash_bundle_inactive_slot(fake_fastboot, tmp_path, bundle):
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        statuses = []
        info = client.flash(manifest=str(bundle / "manifest.yaml"), on_status=statuses.append)
    assert info["state"] == "succeeded"
    # Manifest order, explicit inactive-slot targets, fastbootd for the logical partition.
    assert fake_fastboot.written() == ["abl_b", "boot_b", "vbmeta_b", "system_b"]
    state = fake_fastboot.state
    assert state["vars"]["current-slot"] == "b"  # set_active committed last
    assert "erase:userdata" not in state["commands"]
    assert state["device"]["mode"] == "android"  # finally: continue
    assert any("flash system_b" in s["message"] for s in statuses)
    assert statuses[-1]["phase"] == "complete"


def test_flash_local_files_in_order(fake_fastboot, tmp_path, bundle):
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        info = client.flash({"vbmeta": str(bundle / "vbmeta.img"), "boot": str(bundle / "boot.img")})
    assert info["state"] == "succeeded"
    assert fake_fastboot.written() == ["vbmeta_a", "boot_a"]  # given order, current slot


def test_synthesized_bundle_cannot_write_critical(fake_fastboot, tmp_path, bundle):
    driver = make_driver(fake_fastboot, tmp_path)
    with serve(driver) as client, pytest.raises(Exception, match=r"critical writes \(flash abl\)"):
        client.stage({"abl": str(bundle / "abl.elf")})
    assert fake_fastboot.written() == []


def test_critical_write_to_active_slot_refused(fake_fastboot, tmp_path, bundle):
    (bundle / "manifest.yaml").write_text(MANIFEST.replace("slot: inactive", "slot: current"))
    with serve(make_driver(fake_fastboot, tmp_path)) as client, pytest.raises(Exception, match="active slot"):
        client.flash(manifest=str(bundle / "manifest.yaml"))
    assert fake_fastboot.written() == []


def test_non_ab_critical_refused(fake_fastboot, tmp_path, bundle):
    manifest = MANIFEST.replace("{partition: abl, file: abl.elf}", "{partition: cdt, file: abl.elf}")
    (bundle / "manifest.yaml").write_text(manifest)
    driver = make_driver(fake_fastboot, tmp_path, critical_partitions=["cdt"])
    with serve(driver) as client, pytest.raises(Exception, match="no A/B fallback"):
        client.flash(manifest=str(bundle / "manifest.yaml"))
    assert fake_fastboot.written() == []


def test_requirements_checked(fake_fastboot, tmp_path, bundle):
    (bundle / "manifest.yaml").write_text(MANIFEST.replace("product: testdev", "product: otherdev"))
    with serve(make_driver(fake_fastboot, tmp_path)) as client, pytest.raises(Exception, match="requirement 'product'"):
        client.flash(manifest=str(bundle / "manifest.yaml"))


def test_image_too_large_for_partition(fake_fastboot, tmp_path, bundle):
    with fake_fastboot.edit() as state:
        state["partitions"]["boot_b"] = 1024
    with serve(make_driver(fake_fastboot, tmp_path)) as client, pytest.raises(Exception, match="expands to"):
        client.flash(manifest=str(bundle / "manifest.yaml"))


def test_transient_failures_retried_without_reboot(fake_fastboot, tmp_path, bundle):
    fake_fastboot.inject("fail", "flash:boot_b")
    fake_fastboot.inject("vanish", "flash:vbmeta_b")
    fake_fastboot.inject("hang", "flash:system_b")
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        info = client.flash(manifest=str(bundle / "manifest.yaml"))
    assert info["state"] == "succeeded"
    assert fake_fastboot.written() == ["abl_b", "boot_b", "vbmeta_b", "system_b"]
    reboots = [c for c in fake_fastboot.state["commands"] if c.startswith("reboot")]
    assert reboots == ["reboot fastboot", "reboot bootloader"]  # only the plan's own mode switches


def test_exhausted_retries_leave_device_in_fastboot(fake_fastboot, tmp_path, bundle):
    fake_fastboot.inject("fail", "flash:boot_b", count=10)
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        with pytest.raises(RuntimeError, match="failed_partial"):
            client.flash(manifest=str(bundle / "manifest.yaml"))
        info = client.status()
    assert info["failed_step"] == "flash boot_b"
    assert info["critical_incomplete"] is False
    state = fake_fastboot.state
    assert state["device"]["mode"] == "bootloader" and state["device"]["present"]  # finally skipped
    assert "continue:" not in state["commands"]


def test_lease_end_waits_for_the_flash(fake_fastboot, tmp_path, bundle):
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 0.5
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        job = client.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        wait_for(lambda: fake_fastboot.written())  # first write done
    # The session (as at lease end) closed only after the flash: waited for, not interrupted.
    assert fake_fastboot.written() == ["abl_b", "boot_b", "vbmeta_b", "system_b"]
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        assert client.status(job["job_id"])["state"] == "succeeded"


def crash_runner(job_id):
    """Stop a job's runner as an exporter crash would: mid-step, leaving no result."""
    from jumpstarter.driver.tasks import running_tasks

    (task,) = [t for t in running_tasks() if t.name == f"flash-{job_id}"]
    assert task._handle is not None
    task._handle.get_loop().call_soon_threadsafe(task.cancel)
    wait_for(lambda: not task.running)


def test_interrupted_runner_resumes_from_journal(fake_fastboot, tmp_path, bundle):
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 1.0
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        job = client.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        wait_for(lambda: len(fake_fastboot.written()) >= 2)
        crash_runner(job["job_id"])
        assert client.status(job["job_id"])["state"] in ("stalled", "committing")
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 0
    with serve(make_driver(fake_fastboot, tmp_path)) as client:  # the driver resumes it when used
        info = client.wait_idle(60)
    assert info["state"] == "succeeded"
    written = fake_fastboot.written()
    assert written[-1] == "system_b" and written.count("abl_b") == 1  # completed steps not redone


def test_cancel_refused_after_commit(fake_fastboot, tmp_path, bundle):
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 1.0
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        job = client.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        wait_for(lambda: fake_fastboot.written())
        with pytest.raises(Exception, match="started writing"):
            client.cancel(job["job_id"])
        assert finish(client, job["job_id"])["state"] == "succeeded"


def test_start_is_idempotent(fake_fastboot, tmp_path, bundle):
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        stage_id = client.stage(manifest=str(bundle / "manifest.yaml"))
        first = client.start(stage_id, job_id="ci-run-42")
        second = client.start(stage_id, job_id="ci-run-42")
        assert first["job_id"] == second["job_id"] == "ci-run-42"
        assert finish(client, "ci-run-42")["state"] == "succeeded"
    assert fake_fastboot.written().count("abl_b") == 1


def test_identity_mismatch_aborts(fake_fastboot, tmp_path, bundle):
    with fake_fastboot.edit() as state:
        state["device"]["swap_serial_after_writes"] = 1  # a different board appears on the same port
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        job = client.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        info = finish(client, job["job_id"])
    assert info["state"] == "failed_partial"
    assert "identity mismatch" in info["message"]
    assert len(fake_fastboot.written()) == 1


def test_watch_reattaches(fake_fastboot, tmp_path, bundle):
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        job = client.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        finish(client, job["job_id"])
        replay = list(client.watch(job["job_id"]))
    assert replay[-1]["phase"] == "complete"
    assert any("Writing 'system_b'" in s["message"] for s in replay)


def test_entry_config_validation(fake_fastboot, tmp_path):
    with pytest.raises(ConfigurationError, match="names must be unique"):
        make_driver(fake_fastboot, tmp_path, entry=[{"name": "a", "script": "true"}, {"name": "a", "script": "true"}])
    with pytest.raises(Exception, match="steps"):  # strategies are scripts, not step lists
        make_driver(fake_fastboot, tmp_path, entry=[{"name": "b", "steps": [{"press": "volume_down"}]}])


def test_example_exporter_config_parses():
    from pathlib import Path

    import yaml
    from pydantic import TypeAdapter

    from .entry import EntryStrategy

    example = Path(__file__).parent.parent / "examples" / "exporter.yaml"
    config = yaml.safe_load(example.read_text())["export"]["fastboot"]["config"]
    strategies = TypeAdapter(list[EntryStrategy]).validate_python(config["entry"])
    assert [s.name for s in strategies] == ["adb", "buttons"]
    cleanup = strategies[1].cleanup
    assert cleanup is not None and "volume_down off" in cleanup.script and cleanup.timeout == 30


def test_pinning_config_mirrors_adb(fake_fastboot, tmp_path):
    with pytest.raises(ConfigurationError, match="exactly one of 'usb_port'"):
        make_driver(fake_fastboot, tmp_path, serial="SER123", usb_port="1-2")
    with pytest.raises(ConfigurationError, match="exactly one of 'usb_port'"):
        FastbootFlasher(fastboot_path=fake_fastboot.binary, state_dir=str(tmp_path / "state"))
    assert make_driver(fake_fastboot, tmp_path, usb_port="1-2").usb_port == "usb:1-2"
    assert make_driver(fake_fastboot, tmp_path, usb_port="usb:538116096X").usb_port == "usb:538116096X"


def test_commands_address_the_serial_resolved_from_the_port(fake_fastboot, tmp_path):
    with fake_fastboot.edit() as state:
        state["other_devices"] = [["NEIGHBOR", "usb:1-3"]]  # another DUT on the same host
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        assert client.getvar("product") == "testdev"
    assert set(fake_fastboot.state["selectors"]) == {"SER123"}  # -s SERIAL, never -s usb:<path>


def test_serial_pinning(fake_fastboot, tmp_path, bundle):
    with serve(make_driver(fake_fastboot, tmp_path, serial="SER123")) as client:
        assert client.flash({"boot": str(bundle / "boot.img")})["state"] == "succeeded"
    assert fake_fastboot.written() == ["boot_a"]


def test_missing_device_reports_what_is_visible(fake_fastboot, tmp_path):
    with fake_fastboot.edit() as state:
        state["device"]["present"] = False
        state["other_devices"] = [["NEIGHBOR", "usb:1-3"]]
    with serve(make_driver(fake_fastboot, tmp_path)) as client, pytest.raises(
        Exception, match=r"no fastboot device on USB port usb:1-2.*NEIGHBOR \(usb:1-3\)"
    ):
        client.enter()


def test_flash_runs_as_an_exporter_task(fake_fastboot, tmp_path, bundle):
    from .jobs import runner_timeout
    from jumpstarter.driver.tasks import running_tasks

    with fake_fastboot.edit() as state:
        state["flash_delay"] = 0.5
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        job = client.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        # Waited for at lease end, holds off new leases, bounded past max_job_duration.
        (task,) = wait_for(lambda: running_tasks())
        assert task.reason == f"fastboot flash job {job['job_id']} on usb:1-2"
        assert task.on_lease_end == "wait"
        settings = json.loads((tmp_path / "state" / "jobs" / job["job_id"] / "job.json").read_text())["settings"]
        assert task.timeout == runner_timeout(settings) > settings["max_job_duration"]
    assert running_tasks() == []


class RecordingPower(MockPower):
    """MockPower that records calls, including lifecycle resets."""

    def __post_init__(self):
        super().__post_init__()
        self.calls: list[str] = []

    def reset(self):
        self.calls.append("reset")

    @export
    async def on(self):
        self.calls.append("on")
        await super().on()

    @export
    async def off(self):
        self.calls.append("off")
        await super().off()


def bench(fake, tmp_path, power):
    """An exporter-shaped tree: power exported at top level and referenced by the flasher (ref:)."""
    from jumpstarter_driver_composite.driver import Composite, Proxy

    return Composite(
        children={
            "fastboot": make_driver(fake, tmp_path, children={"power": Proxy(ref="power")}),
            "power": power,
        }
    )


def test_power_is_interlocked_while_flashing(fake_fastboot, tmp_path, bundle):
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 1.0
    power = RecordingPower()
    with serve(bench(fake_fastboot, tmp_path, power)) as client:
        job = client.fastboot.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        wait_for(lambda: fake_fastboot.written())
        with pytest.raises(Exception, match=r"power\.off refused: flash job .* can brick it"):
            client.power.off()  # the top-level export: same instance as the flasher's ref
        assert "off" not in power.calls
        assert client.fastboot.wait_idle(60)["job_id"] == job["job_id"]
        client.power.off()  # allowed again once the job is done
    assert power.calls.count("off") == 1
    assert fake_fastboot.written() == ["abl_b", "boot_b", "vbmeta_b", "system_b"]


def test_reset_not_propagated_to_children_of_an_unfinished_job(fake_fastboot, tmp_path, bundle):
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 1.0
    with serve(make_driver(fake_fastboot, tmp_path, children={"power": RecordingPower()})) as client:
        job = client.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        wait_for(lambda: fake_fastboot.written())
        crash_runner(job["job_id"])  # e.g. the exporter crashed mid-flash
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 0
    # Next session with the flash unfinished: the flasher must not reset its power child.
    power = RecordingPower()
    driver = make_driver(fake_fastboot, tmp_path, children={"power": power})
    with serve(driver) as client:
        assert power.calls == []
        client.wait_idle(60)
    # And once idle, a new session resets children normally.
    power = RecordingPower()
    with serve(make_driver(fake_fastboot, tmp_path, children={"power": power})):
        assert power.calls == ["reset"]


def test_ref_children_are_resolved_before_protection(fake_fastboot, tmp_path):
    """Constructing the flasher with an unresolved ref: child must not fail; it is protected at reset()."""
    from jumpstarter_driver_composite.driver import Composite, Proxy

    tree = Composite(
        children={
            "fastboot": make_driver(
                fake_fastboot,
                tmp_path,
                children={"volume_down": Proxy(ref="volume_down")},
            ),
            "volume_down": MockPower(),
        }
    )
    with serve(tree) as client:  # resolves the ref and installs the interlock
        assert client.fastboot.stages() == []
        assert _guarded(tree.children["volume_down"]) == [True]


def _guarded(*drivers):
    from .interlock import guards_of

    return [bool(guards_of(d)) for d in drivers]


def test_interlock_power_paths_on_a_multi_dut_exporter(fake_fastboot, tmp_path):
    """Power exported separately (the usual layout); only the flashed device's outlet is protected."""
    from jumpstarter_driver_composite.driver import Composite
    from jumpstarter_driver_pyserial.driver import PySerial

    outlet1, outlet2, console = RecordingPower(), RecordingPower(), PySerial(url="loop://")
    tree = Composite(
        children={
            "dut1": make_driver(fake_fastboot, tmp_path, interlock_power=["pdu.outlet1"]),
            "pdu": Composite(children={"outlet1": outlet1, "outlet2": outlet2}),
            "console": console,
        }
    )
    with serve(tree):
        assert _guarded(outlet1, outlet2, console) == [True, False, False]
        assert "DriverCall" not in vars(console)
        assert type(outlet1).DriverCall is type(console).DriverCall  # no class-level change


def test_interlock_power_defaults_to_all(fake_fastboot, tmp_path):
    from jumpstarter_driver_composite.driver import Composite
    from jumpstarter_driver_pyserial.driver import PySerial

    a, b, console = RecordingPower(), RecordingPower(), PySerial(url="loop://")
    tree = Composite(
        children={
            "fastboot": make_driver(fake_fastboot, tmp_path),
            "power": a,
            "pdu": Composite(children={"outlet": b}),
            "console": console,
        }
    )
    with serve(tree):
        assert _guarded(a, b, console) == [True, True, False]


def test_flasher_children_are_protected_regardless_of_power_scope(fake_fastboot, tmp_path):
    from jumpstarter_driver_composite.driver import Composite, Proxy

    own, outlet3, outlet4 = RecordingPower(), RecordingPower(), RecordingPower()
    tree = Composite(
        children={
            "fastboot": make_driver(
                fake_fastboot, tmp_path, interlock_power=["pdu.outlet3"], children={"power": Proxy(ref="own")}
            ),
            "own": own,
            "pdu": Composite(children={"outlet3": outlet3, "outlet4": outlet4}),
        }
    )
    with serve(tree):
        assert _guarded(own, outlet3, outlet4) == [True, True, False]


def test_interlock_scope_path_through_ref(fake_fastboot, tmp_path):
    from jumpstarter_driver_composite.driver import Composite, Proxy

    outlet = RecordingPower()
    tree = Composite(
        children={
            # the path goes through a ref: that is enumerated *after* the flasher
            "fastboot": make_driver(fake_fastboot, tmp_path, interlock_power=["dut.power"]),
            "dut": Composite(children={"power": Proxy(ref="pdu.outlet3")}),
            "pdu": Composite(children={"outlet3": outlet}),
        }
    )
    with serve(tree):
        assert _guarded(outlet) == [True]


def test_interlock_scope_validation(fake_fastboot, tmp_path):
    from jumpstarter_driver_composite.driver import Composite

    for bad in ("auto", "children", "everything"):
        with pytest.raises(ConfigurationError, match="interlock_power must be 'all' or a list"):
            make_driver(fake_fastboot, tmp_path, interlock_power=bad)
    with pytest.raises(ConfigurationError, match="at least one driver path"):
        make_driver(fake_fastboot, tmp_path, interlock_power=[])
    tree = Composite(children={"fastboot": make_driver(fake_fastboot, tmp_path, interlock_power=["pdu.nope"])})
    with pytest.raises(Exception, match="interlock_power path 'pdu.nope' is not in this exporter"), serve(tree):
        pass


class TasmotaLike(RecordingPower):
    """A PowerInterface driver that switches off in reset() and close(), like TasmotaPower."""

    def reset(self):
        self.calls.append("reset->off")

    def close(self):
        self.calls.append("close->off")


def test_every_power_driver_is_protected_during_a_flash(fake_fastboot, tmp_path, bundle):
    from jumpstarter_driver_composite.driver import Composite

    with fake_fastboot.edit() as state:
        state["flash_delay"] = 1.0
    plug = TasmotaLike()
    tree = Composite(children={"fastboot": make_driver(fake_fastboot, tmp_path), "plug": plug})
    with serve(tree) as client:
        assert plug.calls == ["reset->off"]  # idle: session start resets normally
        job = client.fastboot.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        wait_for(lambda: fake_fastboot.written())
        with pytest.raises(Exception, match=r"TasmotaLike\.off refused"):
            client.plug.off()
        crash_runner(job["job_id"])  # e.g. the exporter is stopping: its tasks end mid-flash
    # The session closed with the flash unfinished: the plug's close() (which powers off) is deferred.
    assert plug.calls == ["reset->off"]

    # A session starting with the flash unfinished does not reset it either, and resumes the flash.
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 0
    plug2 = TasmotaLike()
    with serve(Composite(children={"fastboot": make_driver(fake_fastboot, tmp_path), "plug": plug2})) as client:
        assert plug2.calls == []
        client.fastboot.wait_idle(60)

    # Once the flash is safe, the deferred close runs.
    wait_for(lambda: "close->off" in plug.calls, timeout=10)
    assert fake_fastboot.written()[-1] == "system_b"


def _bench(fake, tmp_path, entry):
    """dut1 flasher, nested under a 'bench' composite. Powering on with volume-down held enters fastboot."""
    from jumpstarter_driver_composite.driver import Composite

    with fake.edit() as state:
        state["device"]["present"] = False

    class Button(MockPower):
        def __post_init__(self):
            super().__post_init__()
            self.events = []

        @export
        async def on(self):
            self.events.append("on")
            with fake.edit() as state:
                state["device"]["held"] = True

        @export
        async def off(self):
            self.events.append("off")
            with fake.edit() as state:
                state["device"]["held"] = False

    class DutPower(MockPower):
        @export
        async def on(self):
            with fake.edit() as state:
                state["device"]["present"] = state["device"].get("held", True)

    button = Button()
    flasher = make_driver(fake, tmp_path, entry=entry, children={"power": DutPower(), "volume_down": button})
    return Composite(children={"bench": Composite(children={"dut1": flasher}), "other": MockPower()}), button


HOLD_UNTIL_PRESENT = {
    "name": "buttons",
    "script": "set -e\n"
    "j $JMP_DRIVER_PATH volume_down on\n"
    "j $JMP_DRIVER_PATH power on\n"
    "j $JMP_DRIVER_PATH wait-present --timeout 10  # hold the button until fastboot shows up\n",
    "cleanup": "j $JMP_DRIVER_PATH volume_down off",
    "timeout": 60,
}


def test_entry_strategies_fall_back_and_hold_until_present(fake_fastboot, tmp_path):
    never = {"name": "never", "script": "true", "wait": 0.5}
    tree, button = _bench(fake_fastboot, tmp_path, [never, HOLD_UNTIL_PRESENT])
    with serve(tree) as client:
        result = client.bench.dut1.enter()
    assert result["strategy"] == "buttons" and result["serialno"] == "SER123"
    assert button.events == ["on", "off"]  # held through the wait, released by cleanup


def test_cleanup_runs_when_the_script_fails_or_times_out(fake_fastboot, tmp_path):
    hung = {
        "name": "hung",
        "script": "j $JMP_DRIVER_PATH volume_down on; sleep 30",
        "timeout": 2,
        "cleanup": "j $JMP_DRIVER_PATH volume_down off",
    }
    failing = {**hung, "name": "failing", "script": "j $JMP_DRIVER_PATH volume_down on; exit 3", "timeout": 60}
    tree, button = _bench(fake_fastboot, tmp_path, [hung, failing])
    with serve(tree) as client, pytest.raises(Exception, match=r"(?s)hung: Script timed out.*failing: Script failed"):
        client.bench.dut1.enter()
    assert button.events == ["on", "off", "on", "off"]


def test_wait_present_from_the_client(fake_fastboot, tmp_path):
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        assert client.wait_present() is True
        with fake_fastboot.edit() as state:
            state["device"]["present"] = False
        assert client.wait_present(1) is False


def test_entry_script_drives_other_drivers_with_j(fake_fastboot, tmp_path):
    strategy = {
        "name": "script",
        "script": "set -e\n"
        'test "$JMP_DRIVER_PATH" = "bench dut1" && test "$FASTBOOT_USB_PORT" = "usb:1-2"\n'
        "j other off\n"
        "j $JMP_DRIVER_PATH power on\n"
        'echo "powered up"\n',
        "timeout": 60,
    }
    tree, _ = _bench(fake_fastboot, tmp_path, [strategy])
    with serve(tree) as client:
        result = client.bench.dut1.enter()
    assert result["strategy"] == "script" and result["serialno"] == "SER123"


def test_entry_script_failure_falls_through(fake_fastboot, tmp_path):
    failing = {"name": "broken", "script": "echo 'relay box offline'; exit 3"}
    working = {"name": "script", "script": "j $JMP_DRIVER_PATH power on", "timeout": 60}
    tree, _ = _bench(fake_fastboot, tmp_path, [failing, working])
    with serve(tree) as client:
        assert client.bench.dut1.enter()["strategy"] == "script"


def test_entry_script_errors_are_reported(fake_fastboot, tmp_path):
    tree, _ = _bench(fake_fastboot, tmp_path, [{"name": "s", "script": "echo 'relay box offline'; exit 3"}])
    with serve(tree) as client, pytest.raises(Exception, match=r"(?s)exit code 3.*relay box offline"):
        client.bench.dut1.enter()


def test_entry_script_timeout_kills_it(fake_fastboot, tmp_path):
    marker = tmp_path / "survived"
    tree, _ = _bench(fake_fastboot, tmp_path, [{"name": "s", "script": f"sleep 30; touch {marker}", "timeout": 1}])
    with serve(tree) as client, pytest.raises(Exception, match="timed out after 1"):
        client.bench.dut1.enter()
    assert not marker.exists()


@pytest.mark.parametrize(
    ("block_self", "refusal"),
    [(True, "FastbootFlasher is running its script"), (False, "cannot call enter on its own flasher")],
)
def test_entry_script_cannot_reenter_its_flasher(fake_fastboot, tmp_path, block_self, refusal):
    strategy = {"name": "s", "script": "j $JMP_DRIVER_PATH enter", "timeout": 60, "block_self": block_self}
    tree, _ = _bench(fake_fastboot, tmp_path, [strategy])
    with serve(tree) as client:
        with pytest.raises(Exception, match=refusal):
            client.bench.dut1.enter()
        assert client.bench.dut1.stages() == []  # callable again once the script is done


def test_entry_script_in_python(fake_fastboot, tmp_path):
    import sys

    script = (
        "import os\n"
        "from functools import reduce\n"
        "from jumpstarter.utils.env import env\n"
        "with env() as client:\n"
        "    flasher = reduce(getattr, os.environ['JMP_DRIVER_PATH'].split(), client)\n"
        "    flasher.volume_down.on()\n"
        "    try:\n"
        "        flasher.power.on()\n"
        "        assert flasher.wait_present(10)\n"
        "    finally:\n"
        "        flasher.volume_down.off()\n"
    )
    tree, button = _bench(fake_fastboot, tmp_path, [{"name": "py", "script": script, "exec": sys.executable}])
    with serve(tree) as client:
        assert client.bench.dut1.enter()["strategy"] == "py"
    assert button.events == ["on", "off"]


EXIT_POWER_ON = {
    "script": 'set -e\ntest "$FLASH_JOB_STATE" = succeeded\nj $JMP_DRIVER_PATH power on\n',
    "timeout": 60,
}


def _exit_bench(fake, tmp_path, power, exit_script):
    from jumpstarter_driver_composite.driver import Composite

    flasher = make_driver(fake, tmp_path, exit=exit_script, children={"power": power})
    return Composite(children={"bench": Composite(children={"dut1": flasher})})


def test_exit_script_runs_before_the_lease_is_torn_down(fake_fastboot, tmp_path, bundle):
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 0.5
    power = RecordingPower()
    with serve(_exit_bench(fake_fastboot, tmp_path, power, EXIT_POWER_ON)) as client:
        job = client.bench.dut1.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
    # The lease ended mid-flash: its session stayed up for the flash and the exit script.
    assert "on" in power.calls
    with serve(_exit_bench(fake_fastboot, tmp_path, RecordingPower(), EXIT_POWER_ON)) as client:
        info = client.bench.dut1.status(job["job_id"])
    assert info["state"] == "succeeded" and info["exit"]["state"] == "succeeded"


def test_flash_waits_for_the_exit_script(fake_fastboot, tmp_path, bundle):
    power = RecordingPower()
    with serve(_exit_bench(fake_fastboot, tmp_path, power, EXIT_POWER_ON)) as client:
        info = client.bench.dut1.flash(manifest=str(bundle / "manifest.yaml"))
        assert info["exit"]["state"] == "succeeded"
        assert "on" in power.calls  # done by the time flash() returns; not refused by the interlock


@pytest.mark.parametrize(("when", "expected"), [("success", "skipped"), ("always", "succeeded")])
def test_exit_script_after_a_failed_job(fake_fastboot, tmp_path, bundle, when, expected):
    fake_fastboot.inject("fail", "flash:boot_b", count=10)
    exit_script = {"script": 'test "$FLASH_JOB_STATE" = failed_partial', "when": when}
    with serve(_exit_bench(fake_fastboot, tmp_path, RecordingPower(), exit_script)) as client:
        with pytest.raises(RuntimeError, match="failed_partial"):
            client.bench.dut1.flash(manifest=str(bundle / "manifest.yaml"))
        info = client.bench.dut1.wait_idle(60)
    assert info["exit"]["state"] == expected


# -- sources, staging, and the client --------------------------------------------


def make_archive(tmp_path, bundle, *, manifest=True, prefix="bundle-1.0/"):
    path = tmp_path / "bundle.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for member in sorted(bundle.iterdir()):
            if member.name != "manifest.yaml" or manifest:
                tar.add(member, arcname=prefix + member.name)
    return path


INACTIVE_SLOT_WRITES = ["abl_b", "boot_b", "vbmeta_b", "system_b"]


def test_flash_returns_the_finished_job(fake_fastboot, tmp_path, bundle):
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        info = client.flash(manifest=str(bundle / "manifest.yaml"))  # the job, not a FlashStatus
        assert JobState(info["state"]) is JobState.SUCCEEDED and JobState(info["state"]).terminal
        assert client.status(info["job_id"])["steps_done"] == info["total_steps"]


def test_flash_stream_reports_flash_status(fake_fastboot, tmp_path, bundle):
    archive = make_archive(tmp_path, bundle)
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        statuses = list(client.flash_stream(str(archive)))
    assert all(isinstance(s, FlashStatus) for s in statuses)
    phases = [s.phase for s in statuses]
    assert phases[0] == FlashPhase.DOWNLOAD and FlashPhase.EXTRACT in phases and FlashPhase.CACHE in phases
    assert phases[-1] == FlashPhase.COMPLETE
    assert any(s.step_name == "flash system_b" for s in statuses)
    assert fake_fastboot.written() == INACTIVE_SLOT_WRITES


@pytest.mark.parametrize("source", ["directory", "manifest_file", "archive", "archive_with_manifest"])
def test_every_source_uses_the_same_format(fake_fastboot, tmp_path, bundle, source):
    manifest = str(bundle / "manifest.yaml")
    args = {
        "directory": lambda: ((str(bundle),), {}),
        "manifest_file": lambda: ((), {"manifest": manifest}),
        "archive": lambda: ((str(make_archive(tmp_path, bundle)),), {}),
        # A bare archive (no manifest of its own) takes one from the caller.
        "archive_with_manifest": lambda: ((str(make_archive(tmp_path, bundle, manifest=False, prefix="")),),
                                          {"manifest": manifest}),
    }[source]
    positional, keywords = args()
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        assert client.flash(*positional, wipe=True, **keywords)["state"] == "succeeded"
    assert fake_fastboot.written() == INACTIVE_SLOT_WRITES
    assert fake_fastboot.state["erased"] == ["userdata"]  # the `when: wipe` step ran


def test_flash_single_image_to_a_partition(fake_fastboot, tmp_path, bundle):
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        assert client.flash(str(bundle / "boot.img"), target="boot")["state"] == "succeeded"
    assert fake_fastboot.written() == ["boot_a"]


def test_unknown_step_actions_are_refused_when_staging(fake_fastboot, tmp_path, bundle):
    manifest = MANIFEST.replace("    - fastboot: {reboot: fastboot}\n", "    - qdl: {storage: ufs}\n")
    (bundle / "manifest.yaml").write_text(manifest)
    with serve(make_driver(fake_fastboot, tmp_path)) as client, pytest.raises(Exception, match="unknown action 'qdl'"):
        client.stage(manifest=str(bundle / "manifest.yaml"))
    assert fake_fastboot.written() == []


def test_missing_bundle_files_fail_staging(fake_fastboot, tmp_path, bundle):
    (bundle / "system.img").unlink()
    archive = make_archive(tmp_path, bundle)
    with serve(make_driver(fake_fastboot, tmp_path)) as client, pytest.raises(Exception, match="not in the bundle"):
        client.stage(str(archive))
    assert fake_fastboot.written() == []


def test_no_entry_strategy_fails_before_writing(fake_fastboot, tmp_path, bundle):
    with fake_fastboot.edit() as state:
        state["device"]["present"] = False
    with serve(make_driver(fake_fastboot, tmp_path)) as client, pytest.raises(Exception, match="no entry strategy"):
        client.flash(manifest=str(bundle / "manifest.yaml"))
    assert fake_fastboot.written() == []


@pytest.mark.parametrize(("requires", "error"), [
    ("product: {equals: [gad*, test*]}", None),  # prefix match, any of the values
    ("product: {equals: 'test*', reject: true}", r"requirement 'product': device reports 'testdev', bundle rejects"),
    ("unlocked: {equals: 'no', reject: true}", None),
    ("revision: {equals: evt9, for_product: gadget}", None),  # another product: not checked
    ("revision: {equals: evt9, for_product: testdev}", "the device does not report it"),
])
def test_requires_match_like_android_info(fake_fastboot, tmp_path, bundle, requires, error):
    manifest = MANIFEST.replace("    product: testdev\n", f"    {requires}\n")
    (bundle / "manifest.yaml").write_text(manifest)
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        if error is None:
            assert client.flash(manifest=str(bundle / "manifest.yaml"))["state"] == "succeeded"
        else:
            with pytest.raises(Exception, match=error):
                client.flash(manifest=str(bundle / "manifest.yaml"))
            assert fake_fastboot.written() == []


def test_exit_script_failure_fails_the_flash(fake_fastboot, tmp_path, bundle):
    driver = make_driver(fake_fastboot, tmp_path, exit={"script": "echo nope; exit 3", "timeout": 10})
    with serve(driver) as client, pytest.raises(RuntimeError, match="exit script failed"):
        client.flash(manifest=str(bundle / "manifest.yaml"))
    assert fake_fastboot.written() == INACTIVE_SLOT_WRITES


def test_exit_script_sees_the_job(fake_fastboot, tmp_path, bundle):
    marker = tmp_path / "exit.txt"
    exit_script = {"script": f'echo "$FLASH_JOB_ID $FLASH_JOB_STATE $FASTBOOT_USB_PORT" > {marker}', "timeout": 10}
    with serve(make_driver(fake_fastboot, tmp_path, exit=exit_script)) as client:
        info = client.flash(manifest=str(bundle / "manifest.yaml"), job_id="job-7")
    assert marker.read_text().strip() == "job-7 succeeded usb:1-2" and info["exit"]["state"] == "succeeded"
