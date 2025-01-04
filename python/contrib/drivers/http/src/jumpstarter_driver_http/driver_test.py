from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from jumpstarter.common.utils import serve

from .driver import HttpServer


@pytest.mark.asyncio
async def test_http_server():
    with TemporaryDirectory() as source_dir, TemporaryDirectory() as server_dir:
        server = HttpServer(root_dir=server_dir)
        await server.start()

        with serve(server) as client:
            test_content = b"test content"
            source_file_path = Path(source_dir) / "test.txt"
            source_file_path.write_bytes(test_content)

            uploaded_filename_url = client.put_local_file(str(source_file_path))
            assert uploaded_filename_url == f"{client.get_url()}/test.txt"

            files = client.list_files()
            assert "test.txt" in files

            deleted_filename = client.delete_file("test.txt")
            assert deleted_filename == "test.txt"

            files_after_deletion = client.list_files()
            assert "test.txt" not in files_after_deletion

        await server.stop()
