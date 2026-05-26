import os
import tempfile

from jumpstarter_driver_tftp.driver import Tftp

from jumpstarter.common.utils import serve

with tempfile.TemporaryDirectory() as tmp_dir:
    test_file = os.path.join(tmp_dir, "test.txt")
    with open(test_file, "w") as f:
        _ = f.write("hello")

    with serve(Tftp(root_dir=tmp_dir, host="127.0.0.1", port=6969)) as tftp:
        tftp.start()

        files = list(tftp.storage.list("/"))
        assert "test.txt" in files

        tftp.stop()
