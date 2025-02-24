# TFTP Driver

**driver**: `jumpstarter_driver_tftp.driver.Tftp`

The TFTP driver provides a read-only TFTP server that can be used to serve files.

## Driver Configuration
```yaml
export:
  tftp:
    type: jumpstarter_driver_tftp.driver.Tftp
    config:
      root_dir: /var/lib/tftpboot  # Directory to serve files from
      host: 192.168.1.100          # Host IP to bind to (optional)
      port: 69                     # Port to listen on (optional)
```

### Config parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| root_dir | Root directory for the TFTP server | str | no | "/var/lib/tftpboot" |
| host | IP address to bind the server to | str | no | auto-detect |
| port | Port number to listen on | int | no | 69 |

## TftpServerClient API

```{eval-rst}
.. autoclass:: jumpstarter_driver_tftp.client.TftpServerClient()
   :members:
   :show-inheritance:
```

## Exception Classes

```{eval-rst}
.. autoclass:: jumpstarter_driver_tftp.driver.TftpError
   :members:
   :show-inheritance:

.. autoclass:: jumpstarter_driver_tftp.driver.ServerNotRunning
   :members:
   :show-inheritance:

.. autoclass:: jumpstarter_driver_tftp.driver.FileNotFound
   :members:
   :show-inheritance:
```

## Examples

```{doctest}
>>> import tempfile
>>> import os
>>> from jumpstarter_driver_tftp.driver import Tftp
>>> with tempfile.TemporaryDirectory() as tmp_dir:
...     # Create a test file
...     test_file = os.path.join(tmp_dir, "test.txt")
...     with open(test_file, "w") as f:
...         _ = f.write("hello")
...
...     # Start TFTP server
...     tftp = Tftp(root_dir=tmp_dir, host="127.0.0.1", port=6969)
...     tftp.start()
...
...     # List files
...     files = tftp.list_files()
...     assert "test.txt" in files
...
...     tftp.stop()

```
