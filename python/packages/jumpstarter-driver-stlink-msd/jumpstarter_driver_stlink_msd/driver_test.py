from unittest.mock import patch

import pytest

from .stlink_mount import looks_like_stlink_volume


def test_looks_like_stlink_volume_with_mbed(tmp_path):
    (tmp_path / "MBED.HTM").touch()
    assert looks_like_stlink_volume(tmp_path) is True


def test_looks_like_stlink_volume_with_details(tmp_path):
    (tmp_path / "DETAILS.TXT").touch()
    assert looks_like_stlink_volume(tmp_path) is True


def test_looks_like_stlink_volume_no_markers(tmp_path):
    assert looks_like_stlink_volume(tmp_path) is False


def test_looks_like_stlink_volume_known_prefix(tmp_path):
    nod_dir = tmp_path / "NOD_H755ZI"
    nod_dir.mkdir()
    assert looks_like_stlink_volume(nod_dir) is True


def test_looks_like_stlink_volume_nucleo_prefix(tmp_path):
    nucleo_dir = tmp_path / "NUCLEO"
    nucleo_dir.mkdir()
    assert looks_like_stlink_volume(nucleo_dir) is True


def test_looks_like_stlink_volume_not_dir(tmp_path):
    f = tmp_path / "MBED.HTM"
    f.touch()
    assert looks_like_stlink_volume(f) is False


def test_detect_format():
    from .driver import _detect_format

    assert _detect_format("firmware.elf") == "elf"
    assert _detect_format("firmware.bin") == "bin"
    assert _detect_format("firmware.hex") == "hex"
    assert _detect_format("firmware.unknown") == "unknown"
    assert _detect_format("FIRMWARE.ELF") == "elf"


def test_elf_to_bin_missing_objcopy():
    from .driver import _elf_to_bin

    with pytest.raises(FileNotFoundError, match="No objcopy found"):
        with patch("jumpstarter_driver_stlink_msd.driver._find_objcopy", return_value=None):
            _elf_to_bin("/fake/input.elf", "/fake/output.bin")


def test_elf_to_bin_success(tmp_path):
    from .driver import _elf_to_bin

    elf_file = tmp_path / "test.elf"
    bin_file = tmp_path / "test.bin"
    elf_file.write_bytes(b"\x7fELF" + b"\x00" * 100)

    fake_objcopy = tmp_path / "fake_objcopy.sh"
    fake_objcopy.write_text('#!/bin/sh\ncp "$3" "$4"\n')
    fake_objcopy.chmod(0o755)

    _elf_to_bin(str(elf_file), str(bin_file), objcopy_path=str(fake_objcopy))
    assert bin_file.exists()
