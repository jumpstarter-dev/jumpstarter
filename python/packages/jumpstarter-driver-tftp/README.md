# TFTP driver

`jumpstarter-driver-tftp` provides functionality for a read-only TFTP server
that can be used to serve files.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-tftp
```

## Configuration

Example configuration:

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

| Parameter | Description                        | Type | Required | Default             |
| --------- | ---------------------------------- | ---- | -------- | ------------------- |
| root_dir  | Root directory for the TFTP server | str  | no       | "/var/lib/tftpboot" |
| host      | IP address to bind the server to   | str  | no       | auto-detect         |
| port      | Port number to listen on           | int  | no       | 69                  |

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_tftp.client.TftpServerClient()
   :members:
   :show-inheritance:
```

### Exception Classes

```{eval-rst}
.. autoclass:: jumpstarter_driver_tftp.driver.TftpError
   :members:
   :show-inheritance:

.. autoclass:: jumpstarter_driver_tftp.driver.ServerNotRunning
   :members:
   :show-inheritance:
```

### Examples

```{doctest}
>>> import tempfile
>>> import os
>>> from jumpstarter_driver_tftp.driver import Tftp
>>> from jumpstarter.common.utils import serve
>>> with tempfile.TemporaryDirectory() as tmp_dir:
...     # Create a test file
...     test_file = os.path.join(tmp_dir, "test.txt")
...     with open(test_file, "w") as f:
...         _ = f.write("hello")
...
...     # Start TFTP server
...     with serve(Tftp(root_dir=tmp_dir, host="127.0.0.1", port=6969)) as tftp:
...         tftp.start()
...
...         # List files
...         files = list(tftp.storage.list("/"))
...         assert "test.txt" in files
...
...         tftp.stop()
```
