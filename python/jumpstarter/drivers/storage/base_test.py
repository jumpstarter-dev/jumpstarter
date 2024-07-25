import os
from tempfile import NamedTemporaryFile

import pytest
from anyio.to_thread import run_sync

from jumpstarter.common.grpc import serve
from jumpstarter.drivers.storage import MockStorageMux

pytestmark = pytest.mark.anyio


async def test_drivers_mock_storage_mux():
    async with serve(MockStorageMux(name="storage")) as client:
        with NamedTemporaryFile(delete=False) as file:
            file.write(b"testcontent" * 1000)
            file.close()

            async with client.local_file(file.name) as handle:

                def blocking():
                    client.off()
                    client.dut()
                    client.host()
                    client.write(handle)

                await run_sync(blocking)

            os.unlink(file.name)
