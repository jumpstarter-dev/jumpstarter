from pathlib import Path

import pytest

from .driver import MockStorageMux, MockStorageMuxFlasher
from jumpstarter.common.utils import serve


@pytest.mark.parametrize("driver_type", [MockStorageMux, MockStorageMuxFlasher])
def test_storage_streams_reopen_owned_file_and_cleanup_on_close(driver_type, tmp_path):
    source = tmp_path / "source with spaces.img"
    destination = tmp_path / "readback.img"
    data = b"\x00\xffstorage contents" * 512
    source.write_bytes(data)
    driver = driver_type()
    storage_path = Path(driver.file.name)

    with serve(driver) as client:
        assert storage_path.exists()
        client.write_local_file(str(source))
        client.read_local_file(str(destination))
        assert destination.read_bytes() == data
        assert storage_path.read_bytes() == data

    assert driver.file.closed
    assert not storage_path.exists()
    driver.close()
    assert not storage_path.exists()
