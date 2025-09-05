# Shell driver

`jumpstarter-driver-shell` provides functionality for shell command execution.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-shell
```

## Configuration

Example configuration:

```yaml
export:
  shell:
    type: jumpstarter_driver_shell.driver.Shell
    config:
      methods:
        ls: "ls"
        method2: "echo 'Hello World 2'"
        #multi line method
        method3: |
          echo 'Hello World $1'
          echo 'Hello World $2'
        env_var: "echo $1,$2,$ENV_VAR"
      # optional parameters
      cwd: "/tmp"
      log_level: "INFO"
      shell:
        - "/bin/bash"
        - "-c"
```

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

The shell driver also provides a CLI when using `jmp shell`. All configured methods become available as CLI commands, except for methods starting with `_` which are considered private and hidden from the end user:

```console
$ jmp shell --exporter shell-exporter
$ j shell
Usage: j shell [OPTIONS] COMMAND [ARGS]...

  Shell command executor

Commands:
  env_var  Execute the env_var shell method
  ls       Execute the ls shell method
  method2  Execute the method2 shell method
  method3  Execute the method3 shell method
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
