from tempfile import TemporaryDirectory

import pytest
from opendal import Operator

from jumpstarter.common.utils import serve
from jumpstarter.drivers.storage import MockStorageMux


def test_drivers_mock_storage_mux_fs():
    with serve(MockStorageMux(name="storage")) as client:
        with TemporaryDirectory() as tempdir:
            fs = Operator("fs", root=tempdir)

            fs.write("test", b"testcontent" * 1000)

            client.write_file(fs, "test")


@pytest.mark.skip(reason="require minio")
def test_drivers_mock_storage_mux_s3():
    with serve(MockStorageMux(name="storage")) as client:
        s3 = Operator(
            "s3",
            bucket="test",
            endpoint="http://127.0.0.1:9000",
            region="us-east-1",
            access_key_id="minioadmin",
            secret_access_key="minioadmin",
        )

        s3.write("test", b"testcontent" * 1000)

        client.write_file(s3, "test")
