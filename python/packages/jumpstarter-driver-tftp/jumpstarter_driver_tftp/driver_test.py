import pytest

from jumpstarter_driver_tftp.driver import Tftp

from jumpstarter.common.utils import serve


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def tftp(tmp_path):
    with serve(Tftp(root_dir=str(tmp_path), host="127.0.0.1")) as client:
        try:
            yield client
        finally:
            client.close()


@pytest.mark.anyio
async def test_tftp_file_operations(tftp, tmp_path):
    filename = "test.txt"
    test_data = b"Hello"

    file = tftp.storage.open(filename, "wb")
    file.write_bytes(test_data)
    file.close()

    files = list(tftp.storage.list("/"))
    assert filename in files

    tftp.storage.delete(filename)
    assert filename not in list(tftp.storage.list("/"))


def test_tftp_host_config(tmp_path):
    custom_host = "192.168.1.1"
    server = Tftp(root_dir=str(tmp_path), host=custom_host)
    assert server.get_host() == custom_host


def test_tftp_root_directory_creation(tmp_path):
    new_dir = tmp_path / "new_tftp_root"
    server = Tftp(root_dir=str(new_dir))
    assert new_dir.exists()
    server.close()
