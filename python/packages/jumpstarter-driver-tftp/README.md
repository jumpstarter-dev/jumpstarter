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


### Example Test

```{literalinclude} ../../../../../packages/jumpstarter-driver-tftp/examples/tftp_test.py
:language: python
```

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

```{literalinclude} ../../../../../packages/jumpstarter-driver-tftp/examples/usage_server.py
:language: python
```
