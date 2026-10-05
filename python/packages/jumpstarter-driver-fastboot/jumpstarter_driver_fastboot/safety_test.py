"""Brick-prevention checklist for the fastboot flasher.

Every mechanism below must keep holding for the fastboot driver. Each is
pinned by a test, here or elsewhere:

Before anything is written (staging and preflight)
  - blobs digest-verified, decompressed, sparse headers checked ... store_test.py
  - free space checked before staging ............................ store_test.py
  - bundle paths can't escape the bundle .......................... plan_test::test_path_traversal_rejected
  - every referenced file staged .................................. plan_test::test_missing_file_rejected
  - ``requires`` checked against getvar ........................... driver_test::test_requirements_checked
  - the build's own android-info.txt checked as fastboot does it .. test_android_info_requirements_are_checked
  - image larger than its partition refused ....................... driver_test::test_image_too_large...
  - logical partition flashed outside fastbootd refused ........... test_logical_partition_needs_fastbootd
  - critical partitions by pattern ................................ plan_test::test_critical_partitions_by_pattern
  - critical write to the active slot refused ..................... driver_test::test_critical_write_to_active...
  - critical write without an A/B fallback refused ................ driver_test::test_non_ab_critical_refused
  - images without a manifest can't write critical partitions ..... driver_test::test_synthesized_bundle_cannot...
  - oem lock/unlock never allowed; others only if allowlisted ..... plan_test::test_lock_unlock_never_allowed,
                                                                    plan_test::test_oem_requires_allowlist
While writing (the job runner)
  - the device is addressed by -s SERIAL resolved from the port ... driver_test::test_commands_address_the_serial...
  - serialno re-checked before every step ......................... driver_test::test_identity_mismatch_aborts
  - retry without reboot; the device may drop off USB ............. driver_test::test_transient_failures_retried...
  - device gone too long: interrupted, resumable .................. test_device_gone_too_long_interrupts_then_resumes
  - oem commands are never retried ................................ test_failed_oem_is_not_retried
  - max_job_duration bounds the job ............................... test_max_job_duration_stops_between_steps
  - host-side abort stops between steps ........................... test_host_abort_stops_between_steps
  - on failure the device stays in fastboot, finally skipped ...... driver_test::test_exhausted_retries_leave...
  - journaled; resumed, completed steps not redone ................ driver_test::test_interrupted_runner_resumes...
  - cancel refused once the first write began ..................... driver_test::test_cancel_refused_after_commit
  - only one job per device; device calls refused meanwhile ....... test_device_calls_refused_while_a_job_owns_it
Around the job (the lease and the bench)
  - power off refused while writing (all, or listed paths) ........ driver_test::test_power_is_interlocked...,
                                                                    driver_test::test_interlock_power_paths_on_a_multi_dut...
  - session reset/close can't power the device off mid-flash ...... driver_test::test_every_power_driver_is_protected...
  - the lease's end waits for the job ............................. driver_test::test_lease_end_waits_for_the_flash
  - entry scripts can't re-enter their flasher .................... driver_test::test_entry_script_cannot_reenter...
  - exit script runs after success only, unless ``when: always`` .. driver_test::test_exit_script_after_a_failed_job
"""

import pytest

from .conftest import wait_for
from .driver_test import MANIFEST, bundle, finish, make_driver  # noqa: F401 - bundle is a fixture
from jumpstarter.common.utils import serve


def test_logical_partition_needs_fastbootd(fake_fastboot, tmp_path, bundle):  # noqa: F811
    manifest = MANIFEST.replace("    - fastboot: {reboot: fastboot}\n", "").replace(
        "    - fastboot: {reboot: bootloader}\n", ""
    )
    (bundle / "manifest.yaml").write_text(manifest)
    with serve(make_driver(fake_fastboot, tmp_path)) as client, pytest.raises(
        Exception, match=r"system_b is a logical partition; add a 'fastboot: \{reboot: fastboot\}' step"
    ):
        client.flash(manifest=str(bundle / "manifest.yaml"))
    assert fake_fastboot.written() == []


def test_device_calls_refused_while_a_job_owns_it(fake_fastboot, tmp_path, bundle):  # noqa: F811
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 1.0
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        stage_id = client.stage(manifest=str(bundle / "manifest.yaml"))
        job = client.start(stage_id)
        wait_for(lambda: fake_fastboot.written())
        for call in (lambda: client.getvar("product"), client.enter, client.reboot,
                     lambda: client.start(stage_id)):
            with pytest.raises(Exception, match=f"owned by flash job {job['job_id']}"):
                call()
        assert finish(client, job["job_id"])["state"] == "succeeded"
    assert fake_fastboot.written().count("abl_b") == 1  # nothing ran twice


def test_failed_oem_is_not_retried(fake_fastboot, tmp_path, bundle):  # noqa: F811
    manifest = MANIFEST.replace("    - fastboot: {set_active: inactive}\n", "    - fastboot: {oem: device-info}\n")
    (bundle / "manifest.yaml").write_text(manifest)
    fake_fastboot.inject("fail", "oem:device-info")
    driver = make_driver(fake_fastboot, tmp_path, allowed_oem_commands=["device-info"])
    with serve(driver) as client, pytest.raises(RuntimeError, match="failed_partial"):
        client.flash(manifest=str(bundle / "manifest.yaml"))
    assert [c for c in fake_fastboot.state["commands"] if c.startswith("oem")] == ["oem device-info"]


def test_max_job_duration_stops_between_steps(fake_fastboot, tmp_path, bundle):  # noqa: F811
    with fake_fastboot.edit() as state:
        state["flash_delay"] = 0.6
    driver = make_driver(fake_fastboot, tmp_path, max_job_duration=1.0)
    with serve(driver) as client, pytest.raises(RuntimeError, match="max_job_duration"):
        client.flash(manifest=str(bundle / "manifest.yaml"))
    state = fake_fastboot.state
    assert 0 < len(fake_fastboot.written()) < 4
    assert state["device"]["present"] and state["device"]["mode"] != "android"  # left in fastboot, not booted


def test_host_abort_stops_between_steps(fake_fastboot, tmp_path, bundle):  # noqa: F811
    from .runner import main

    with fake_fastboot.edit() as state:
        state["flash_delay"] = 0.8
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        job = client.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        wait_for(lambda: fake_fastboot.written())
        with pytest.raises(SystemExit):
            main(["abort", str(tmp_path / "state" / "jobs" / job["job_id"])])  # --force-unsafe is required
        assert main(["abort", str(tmp_path / "state" / "jobs" / job["job_id"]), "--force-unsafe"]) == 0
        info = finish(client, job["job_id"])
    assert info["state"] == "failed_partial" and "aborted on the exporter host" in info["message"]
    assert len(fake_fastboot.written()) < 4
    assert fake_fastboot.state["device"]["mode"] != "android"


def test_device_gone_too_long_interrupts_then_resumes(fake_fastboot, tmp_path, bundle):  # noqa: F811
    with fake_fastboot.edit() as state:
        state["vanish_seconds"] = 4
    fake_fastboot.inject("vanish", "flash:vbmeta_b")
    with serve(make_driver(fake_fastboot, tmp_path, reenumerate_timeout=1, step_retries=1)) as client:
        job = client.flash(manifest=str(bundle / "manifest.yaml"), wait=False)
        info = finish(client, job["job_id"])
        assert info["state"] == "interrupted" and info["failed_step"] == "flash vbmeta_b"
        wait_for(lambda: client.wait_present(0), timeout=10)  # back in fastboot (re-entered by hand)
        client.resume(job["job_id"])
        assert finish(client, job["job_id"])["state"] == "succeeded"
    written = fake_fastboot.written()
    assert written.count("abl_b") == 1 and written[-1] == "system_b"


@pytest.mark.parametrize(("android_info", "error"), [
    ("require board=testdev\nrequire version-bootloader=abl-1.*\n", None),
    ("require board=otherdev|thirddev\n", "requirement 'product': device reports 'testdev'"),
    ("reject version-bootloader=abl-1.4\n", "bundle rejects"),
    ("require-for-product:testdev version-bootloader=abl-9\n", "requirement 'version-bootloader'"),
    ("require-for-product:otherdev version-bootloader=abl-9\n", None),  # not this product: not checked
])
def test_android_info_requirements_are_checked(fake_fastboot, tmp_path, bundle, android_info, error):  # noqa: F811
    with fake_fastboot.edit() as state:
        state["vars"]["version-bootloader"] = "abl-1.4"
    (bundle / "android-info.txt").write_text(android_info)
    option = "    android_info: {file: android-info.txt}\n"
    manifest = MANIFEST.replace("    finally: continue\n", "    finally: continue\n" + option)
    (bundle / "manifest.yaml").write_text(manifest)
    with serve(make_driver(fake_fastboot, tmp_path)) as client:
        if error is None:
            assert client.flash(manifest=str(bundle / "manifest.yaml"))["state"] == "succeeded"
        else:
            with pytest.raises(Exception, match=error):
                client.flash(manifest=str(bundle / "manifest.yaml"))
            assert fake_fastboot.written() == []
