# TFTP Driver

`jumpstarter-driver-tftp` provides functionality for a read-only TFTP server
that can be used to serve files.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-tftp
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-tftp/examples/config.yaml
:language: yaml
```

### Config parameters

| Parameter               | Description                                                      | Type | Required | Default             |
| ----------------------- | ---------------------------------------------------------------- | ---- | -------- | ------------------- |
| root_dir                | Root directory for the TFTP server                               | str  | no       | "/var/lib/tftpboot" |
| host                    | IP address to bind the server to                                 | str  | no       | auto-detect         |
| port                    | Port number to listen on                                         | int  | no       | 69                  |
| remove_created_on_close | Automatically remove created files/directories when driver closes| bool | no       | true                |

### File Management

The TFTP server driver automatically tracks files and directories created during the session. By default, `remove_created_on_close` is set to `true` to clean up temporary boot files automatically. Set to `false` if you want to preserve boot files and firmware images that are reused across sessions.

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
