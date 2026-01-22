import logging

import pytest
from jumpstarter_driver_tftp.driver import TftpError
from jumpstarter_testing.pytest import JumpstarterTest

log = logging.getLogger(__name__)


class TestResource(JumpstarterTest):
    selector = "board=rpi4"

    @pytest.fixture()
    def setup_tftp(self, client):
        # Move the setup code to a fixture
        client.tftp.start()
        yield client
        client.tftp.stop()

    def test_tftp_operations(self, setup_tftp):
        client = setup_tftp
        test_file = "test.bin"

        # Create test file
        with open(test_file, "wb") as f:
            f.write(b"Hello from TFTP streaming test!")

        try:
            # Test upload
            client.tftp.put_local_file(test_file)
            assert test_file in client.tftp.list_files()

            # Test delete
            client.tftp.delete_file(test_file)
            assert test_file not in client.tftp.list_files()

        except (TftpError, FileNotFoundError) as e:
            pytest.fail(f"Test failed: {e}") # ty: ignore[call-non-callable]
