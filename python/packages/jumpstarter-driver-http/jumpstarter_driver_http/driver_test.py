import aiohttp
import pytest

from .driver import HttpServer
from jumpstarter.common.utils import serve


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def http(tmp_path):
    with serve(HttpServer(root_dir=str(tmp_path))) as client:
        client.start()
        try:
            yield client
        finally:
            client.stop()


@pytest.mark.anyio
async def test_http_server(http, tmp_path):
    filename = "test.txt"
    test_content = b"test content"

    (tmp_path / "src").write_bytes(test_content)

    uploaded_url = http.put_file(filename, tmp_path / "src")

    print(http.storage.stat(filename))

    files = list(http.storage.list("/"))
    assert filename in files

    async with aiohttp.ClientSession() as session:
        async with session.get(uploaded_url) as response:
            assert response.status == 200
            retrieved_content = await response.read()
            assert retrieved_content == test_content

    http.storage.delete(filename)

    files_after_deletion = list(http.storage.list("/"))
    assert filename not in files_after_deletion


def test_http_server_host_config(tmp_path):
    custom_host = "192.168.1.1"
    server = HttpServer(root_dir=str(tmp_path), host=custom_host)
    assert server.get_host() == custom_host


def test_http_server_root_directory_creation(tmp_path):
    new_dir = tmp_path / "new_http_root"
    _ = HttpServer(root_dir=str(new_dir))
    assert new_dir.exists()
