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
