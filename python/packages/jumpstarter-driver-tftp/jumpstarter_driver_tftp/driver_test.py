import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional
from uuid import uuid4

import anyio
import pytest
from anyio import create_memory_object_stream

from jumpstarter_driver_tftp.driver import (
    FileNotFound,
    Tftp,
    TftpError,
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
async def test_tftp_checksum_validation(server):
    filename = "test_checksum.txt"
    test_data = b"Hello world"
    modified_data = b"Modified content"

    def compute_checksum(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    initial_checksum = await _upload_file(server, filename, test_data)
    assert filename in server.list_files()
    assert compute_checksum(test_data) == initial_checksum

    # Second upload with same data should be skipped
    same_data_checksum = await _upload_file(server, filename, test_data)
    assert same_data_checksum == initial_checksum

    modified_checksum = await _upload_file(server, filename, modified_data)
    assert modified_checksum != initial_checksum
    assert Path(server.root_dir).joinpath(filename).read_bytes() == modified_data

    empty_checksum = await _upload_file(server, "empty.txt", b"")
    assert empty_checksum == hashlib.sha256(b"").hexdigest()

@pytest.mark.anyio
async def test_tftp_detect_corrupted_file(server):
    filename = "corrupted.txt"
    original_data = b"Original Data"
    client_checksum = hashlib.sha256(original_data).hexdigest()

    await _upload_file(server, filename, original_data)
    assert server.check_file_checksum(filename, client_checksum)

    file_path = Path(server.root_dir, filename)
    with open(file_path, "wb") as f:
        f.write(b"Corrupted Data")

    assert not server.check_file_checksum(filename, client_checksum)

@pytest.mark.anyio
async def test_tftp_reupload_different_checksum(server):
    filename = "reupload.txt"
    initial_data = b"Initial Data"
    updated_data = b"Updated Data"
    initial_checksum = hashlib.sha256(initial_data).hexdigest()
    updated_checksum = hashlib.sha256(updated_data).hexdigest()

    await _upload_file(server, filename, initial_data)
    assert server.check_file_checksum(filename, initial_checksum)
    assert Path(server.root_dir, filename).read_bytes() == initial_data

    await _upload_file(server, filename, updated_data, client_checksum=updated_checksum)
    assert server.check_file_checksum(filename, updated_checksum)
    assert Path(server.root_dir, filename).read_bytes() == updated_data

@pytest.fixture
def anyio_backend():
    return "asyncio"

async def _upload_file(server, filename: str, data: bytes, client_checksum: Optional[str] = None) -> str:
    send_stream, receive_stream = create_memory_object_stream()
    resource_uuid = uuid4()
    server.resources[resource_uuid] = receive_stream
    resource_handle = ClientStreamResource(uuid=resource_uuid).model_dump(mode="json")
    client_checksum = client_checksum or hashlib.sha256(data).hexdigest()

    async def send_data():
        await send_stream.send(data)
        await send_stream.aclose()

    async with anyio.create_task_group() as tg:
        tg.start_soon(send_data)
        await server.put_file(filename, resource_handle, client_checksum)

    return server._compute_checksum(os.path.join(server.root_dir, filename))

@pytest.mark.anyio
async def test_tftp_path_traversal_attempt(server):
    malicious_filename = "../../evil.txt"

    resource_uuid = uuid4()
    server.resources[resource_uuid] = None
    resource_handle = ClientStreamResource(uuid=resource_uuid).model_dump(mode="json")

    with pytest.raises(TftpError, match="Invalid target path"):
        await server.put_file(malicious_filename, resource_handle, "checksum")
