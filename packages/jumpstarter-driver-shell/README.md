# Shell driver

`jumpstarter-driver-shell` provides functionality for shell command execution.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-shell
```

## Configuration

The shell driver supports two configuration formats for methods:

### Format 1: Simple String e.g. for self-descriptive short commands

```yaml
export:
  shell:
    type: jumpstarter_driver_shell.driver.Shell
    config:
      methods:
        ls: "ls"
        echo_hello: "echo 'Hello World'"
```

### Format 2: Unified Format with Descriptions

```yaml
export:
  shell:
    type: jumpstarter_driver_shell.driver.Shell
    config:
      methods:
        ls:
          command: "ls -la"
          description: "List directory contents with details"
        deploy:
          command: "ansible-playbook deploy.yml"
          description: "Deploy application using Ansible"
        # Multi-line commands work too
        setup:
          command: |
            echo 'Setting up environment'
            export PATH=$PATH:/usr/local/bin
            ./setup.sh
          description: "Set up the development environment"
        # Description-only (uses default "echo Hello" command)
        placeholder:
          description: "Placeholder method for testing"
        # Custom timeout for long-running operations
        long_backup:
          command: "tar -czf backup.tar.gz /data && rsync backup.tar.gz remote:/backups/"
          description: "Create and sync backup (may take a while)"
          timeout: 1800  # 30 minutes instead of default 5 minutes
        # You can mix both formats
        simple_echo: "echo 'simple'"
      # optional parameters
      cwd: "/tmp"
      log_level: "INFO"
      shell:
        - "/bin/bash"
        - "-c"
```

### Configuration Parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| `methods` | Dictionary of methods. Values can be:<br/>- String: just the command<br/>- Dict: `{command: "...", description: "...", timeout: ...}` | `dict[str, str \| dict]` | Yes | - |
| `cwd` | Working directory for shell commands | `str` | No | `None` |
| `log_level` | Logging level | `str` | No | `"INFO"` |
| `shell` | Shell command to execute scripts | `list[str]` | No | `["bash", "-c"]` |
| `timeout` | Command timeout in seconds | `int` | No | `300` |

**Method Configuration Options:**

For the dict format, each method supports:
- `command`: The shell command to execute (optional, defaults to `"echo Hello"`)
- `description`: CLI help text (optional, defaults to `"Execute the {method_name} shell method"`)
- `timeout`: Command-specific timeout in seconds (optional, defaults to global `timeout` value)

**Note:** You can mix both formats in the same configuration - use string format for simple commands and dict format when you want custom descriptions or timeouts.

## API Reference

Assuming the exporter driver is configured as in the example above, the client
methods will be generated dynamically, and they will be available as follows:

```{eval-rst}
.. autoclass:: jumpstarter_driver_shell.client.ShellClient
    :members:

.. function:: ls()
   :noindex:

   :returns: A tuple(stdout, stderr, return_code)

.. function:: method2()
    :noindex:

    :returns: A tuple(stdout, stderr, return_code)

.. function:: method3(arg1, arg2)
    :noindex:

    :returns: A tuple(stdout, stderr, return_code)

.. function:: env_var(arg1, arg2, ENV_VAR="value")
    :noindex:

    :returns: A tuple(stdout, stderr, return_code)
```

## CLI Usage

The shell driver also provides a CLI when using `jmp shell`. All configured methods become available as CLI commands, except for methods starting with `_` which are considered private and hidden from the end user.

### CLI Help Output

With unified format (custom descriptions):

```console
$ jmp shell --exporter shell-exporter
$ j shell
Usage: j shell [OPTIONS] COMMAND [ARGS]...

  Shell command executor

Commands:
  deploy  Deploy application using Ansible
  ls      List directory contents with details
  setup   Set up the development environment
```

With simple string format (default descriptions):

```console
$ j shell
Usage: j shell [OPTIONS] COMMAND [ARGS]...

  Shell command executor

Commands:
  deploy  Execute the deploy shell method
  ls      Execute the ls shell method
  setup   Execute the setup shell method
```

**Mixed format example:**

```yaml
methods:
  deploy:
    command: "ansible-playbook deploy.yml"
    description: "Deploy using Ansible"
  restart: "systemctl restart myapp"  # Simple format
```

Results in:
```console
Commands:
  deploy   Deploy using Ansible
  restart  Execute the restart shell method
```

### CLI Command Usage

Each configured method becomes a CLI command with the following options:

```console
$ j shell ls --help
Usage: j shell ls [OPTIONS] [ARGS]...

  Execute the ls shell method

Options:
  -e, --env TEXT  Environment variables in KEY=VALUE format
  --help          Show this message and exit.
```

### Examples

```console
# Execute simple commands
$ j shell ls
file1.txt  file2.txt  directory/

# Pass arguments to shell methods
$ j shell method3 "first arg" "second arg"
Hello World first arg
Hello World second arg

# Set environment variables
$ j shell env_var arg1 arg2 --env ENV_VAR=myvalue
arg1,arg2,myvalue
```
