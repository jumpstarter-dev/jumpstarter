import hashlib
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import anyio
import pytest
from anyio import create_memory_object_stream

from jumpstarter_driver_tftp.driver import (
    FileNotFound,
    Tftp,
)

from jumpstarter.common.resources import ClientStreamResource


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def server(temp_dir):
    server = Tftp(root_dir=temp_dir, host="127.0.0.1")
    yield server
    server.close()


@pytest.mark.anyio
async def test_tftp_file_operations(server):
    filename = "test.txt"
    test_data = b"Hello"
    client_checksum = hashlib.sha256(test_data).hexdigest()

    send_stream, receive_stream = create_memory_object_stream(max_buffer_size=10)

    resource_uuid = uuid4()
    server.resources[resource_uuid] = receive_stream

    resource_handle = ClientStreamResource(uuid=resource_uuid).model_dump(mode="json")

    async def send_data():
        await send_stream.send(test_data)
        await send_stream.aclose()

    async with anyio.create_task_group() as tg:
        tg.start_soon(send_data)
        await server.put_file(filename, resource_handle, client_checksum)

    files = server.list_files()
    assert filename in files

    file_path = Path(server.root_dir) / filename
    assert file_path.read_bytes() == test_data

    server.delete_file(filename)
    assert filename not in server.list_files()

    with pytest.raises(FileNotFound):
        server.delete_file("nonexistent.txt")


def test_tftp_host_config(temp_dir):
    custom_host = "192.168.1.1"
    server = Tftp(root_dir=temp_dir, host=custom_host)
    assert server.get_host() == custom_host


def test_tftp_root_directory_creation(temp_dir):
    new_dir = os.path.join(temp_dir, "new_tftp_root")
    server = Tftp(root_dir=new_dir)
    assert os.path.exists(new_dir)
    server.close()


@pytest.mark.anyio
async def test_tftp_detect_corrupted_file(server):
    filename = "corrupted.txt"
    original_data = b"Original Data"
    client_checksum = hashlib.sha256(original_data).hexdigest()

    await _upload_file(server, filename, original_data)

    assert server.check_file_checksum(filename, client_checksum)

    file_path = Path(server.root_dir, filename)
    file_path.write_bytes(b"corrupted Data")

    assert not server.check_file_checksum(filename, client_checksum)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _upload_file(server, filename: str, data: bytes) -> str:
    send_stream, receive_stream = create_memory_object_stream()
    resource_uuid = uuid4()
    server.resources[resource_uuid] = receive_stream
    resource_handle = ClientStreamResource(uuid=resource_uuid).model_dump(mode="json")

    async def send_data():
        await send_stream.send(data)
        await send_stream.aclose()

    async with anyio.create_task_group() as tg:
        tg.start_soon(send_data)
        await server.put_file(filename, resource_handle, hashlib.sha256(data).hexdigest())

    return hashlib.sha256(data).hexdigest()
