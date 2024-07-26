import os
from tempfile import NamedTemporaryFile

from jumpstarter.common.utils import serve
from jumpstarter.drivers.storage import MockStorageMux


def test_drivers_mock_storage_mux():
    with serve(MockStorageMux(name="storage")) as client:
        with NamedTemporaryFile(delete=False) as file:
            file.write(b"testcontent" * 1000)
            file.close()

            with client.local_file(file.name) as handle:
                client.off()
                client.dut()
                client.host()
                client.write(handle)

            os.unlink(file.name)
