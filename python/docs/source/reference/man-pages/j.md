# j

The `j` command is available within the Jumpstarter shell environment (launched
via `jmp shell`). It provides access to driver CLI interfaces configured in your
exporter.

Usage:

```console
$ j [OPTIONS] COMMAND [ARGS]...
```

The available commands depend on which drivers are loaded in your current
session. When you run the `j` command in the shell:

- Use `j` alone to see all available driver interfaces
- Access specific drivers with `j <driver_type> <action>`
- Each driver exposes different commands through this interface

Available commands vary depending on your configured drivers.
