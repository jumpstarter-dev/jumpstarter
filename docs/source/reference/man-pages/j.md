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
- Access specific drivers with `j <driver_name> <action>`, where
  `<driver_name>` is the name of the driver under `export` in the exporter
  configuration
- Each driver exposes different commands through this interface
- Drivers whose client package is not installed locally, or that the client
  configuration's `drivers.allow` list does not permit, are not listed

Available commands vary depending on your configured drivers.

`j`, and the command groups of composite drivers that use the default composite
command line, accept `--log-level` (`DEBUG`, `INFO`, `WARNING`, `ERROR` or
`CRITICAL`), which sets the level of every logger in the `j` process:

```console
$ j --log-level DEBUG power on
```
