import os
from tempfile import NamedTemporaryFile

from opendal import Operator

from jumpstarter.common.utils import serve
from jumpstarter.drivers.storage import MockStorageMux


def test_drivers_mock_storage_mux():
    with serve(MockStorageMux(name="storage")) as client:
        with NamedTemporaryFile(delete=False) as file:
            file.write(b"testcontent" * 1000)
            file.close()

            client.off()
            client.dut()
            client.host()
            # client.write_local_file(file.name)

            op = Operator("fs", root="/")
            client.write_file(op, file.name)

            os.unlink(file.name)
