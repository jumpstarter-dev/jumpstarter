# HTTP driver

`jumpstarter-driver-http` provides functionality for HTTP communication.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-http
```

## Configuration

Example configuration:

```yaml
export:
  http:
    type: jumpstarter_driver_http.driver.HttpServer
    config:
      root_dir: "/var/www"
      host: "0.0.0.0"
      port: 8080
      timeout: 600
      remove_created_on_close: true  # Clean up temporary files on close
```

### Config parameters

| Parameter               | Description                                                      | Type | Required | Default           |
| ----------------------- | ---------------------------------------------------------------- | ---- | -------- | ----------------- |
| root_dir                | Root directory for serving files                                 | str  | no       | "/var/www"        |
| host                    | IP address to bind the server to                                 | str  | no       | None (auto-detect)|
| port                    | Port number to listen on                                         | int  | no       | 8080              |
| timeout                 | Request timeout in seconds                                       | int  | no       | 600               |
| remove_created_on_close | Automatically remove created files/directories when driver closes| bool | no       | true              |

### File Management

The internal HTTP server driver automatically tracks files and directories created during the session. When `remove_created_on_close` is enabled (default), all tracked resources are cleaned up when the driver closes.

## API Reference

Add API documentation here.
