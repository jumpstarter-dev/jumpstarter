import os

from .driver import PiPicoFlasher
from jumpstarter.common.utils import serve


def test_flash_flushes_a_writable_completed_file(monkeypatch, tmp_path):
    boot = tmp_path / "BOOTSEL volume"
    boot.mkdir()
    firmware = tmp_path / "firmware.uf2"
    data = b"UF2\x00\xff" * 1024
    firmware.write_bytes(data)
    monkeypatch.setattr("jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts", lambda: [boot])
    real_fsync = os.fsync
    flushed_sizes = []

    def flush_writable_file(fd):
        # A zero-byte write checks access without changing firmware bytes. This
        # catches read-only flush handles on POSIX too, where fsync permits them.
        os.write(fd, b"")
        flushed_sizes.append(os.fstat(fd).st_size)
        real_fsync(fd)

    monkeypatch.setattr("jumpstarter_driver_pi_pico.driver.os.fsync", flush_writable_file)
    with serve(PiPicoFlasher()) as client:
        client.flash(firmware)

    assert flushed_sizes == [len(data)]
    assert (boot / "Firmware.uf2").read_bytes() == data
