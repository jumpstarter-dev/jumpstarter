import os
import uuid
from tempfile import TemporaryDirectory

import aiohttp
import anyio
import pytest
from anyio import create_memory_object_stream
from jumpstarter.common.resources import ClientStreamResource

from .driver import HttpServer


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def temp_dir():
    with TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
async def server(temp_dir):
    server = HttpServer(root_dir=temp_dir)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_http_server(server):
    filename = "test.txt"
    test_content = b"test content"

    send_stream, receive_stream = create_memory_object_stream(max_buffer_size=1024)

    resource_uuid = uuid.uuid4()
    server.resources[resource_uuid] = receive_stream

    resource_handle = ClientStreamResource(uuid=resource_uuid).model_dump(mode="json")

    async def send_data():
        await send_stream.send(test_content)
        await send_stream.aclose()

    async with anyio.create_task_group() as tg:
        tg.start_soon(send_data)

        uploaded_url = await server.put_file(filename, resource_handle)
        assert uploaded_url == f"{server.get_url()}/{filename}"

    files = server.list_files()
    assert filename in files

    async with aiohttp.ClientSession() as session:
        async with session.get(uploaded_url) as response:
            assert response.status == 200
            retrieved_content = await response.read()
            assert retrieved_content == test_content

    deleted_filename = await server.delete_file(filename)
    assert deleted_filename == filename

    files_after_deletion = server.list_files()
    assert filename not in files_after_deletion


def test_http_server_host_config(temp_dir):
    custom_host = "192.168.1.1"
    server = HttpServer(root_dir=temp_dir, host=custom_host)
    assert server.get_host() == custom_host


def test_http_server_root_directory_creation(temp_dir):
    new_dir = os.path.join(temp_dir, "new_http_root")
    _ = HttpServer(root_dir=new_dir)
    assert os.path.exists(new_dir)
