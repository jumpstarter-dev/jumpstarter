# Shell

## Starting and Exiting a Session

Start a {term}`local mode` {term}`exporter` {term}`session`:
```console
$ jmp shell --exporter example-local
```

Start a {term}`distributed mode` {term}`exporter` {term}`session`:
```console
$ jmp shell --client hello --selector example.com/board=foo
```

When finished, simply exit the shell:
```console
$ exit
```

## Interact with the Exporter Shell

The {term}`exporter shell` provides access to driver CLI interfaces through the magic
{term}`j` command:

```console
$ jmp shell # Use appropriate --exporter or --client parameters
$ j
Usage: j [OPTIONS] COMMAND [ARGS]...

  Generic composite device

Options:
  --log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]
                                  Set the log level
  --help                          Show this message and exit.

Commands:
  power    Generic power
  storage  Storage operations
$ j power on
$ j power off
$ exit
```

When you run the `j` command in the {term}`exporter shell`, you're accessing the CLI
interfaces exposed by the drivers configured in your {term}`exporter`. In this example:

- `j power` - Would access the power interface from the MockPower driver
- `j storage` - Would access the storage interface from the MockStorageMux
  driver

Each driver can expose different commands through this interface, making it easy
to interact with the mock hardware. The command structure follows `j
<name> <action>`, where `<name>` is the driver's key under `export` in the
exporter config and available actions depend on the specific driver.

## The Shell Prompt

In bash, zsh and fish, the {term}`exporter shell` prompt is displayed as
`<directory> ⚡<exporter> ➤`, where `<directory>` is the name of the current
directory and `<exporter>` is the exporter's name (`local` in {term}`local mode`,
`direct` in {term}`direct mode`). Set the `NO_ICONS` environment variable to any
value (including empty) to get an ASCII prompt instead:

```console
$ NO_ICONS=1 jmp shell
<directory> ^<exporter> >
```

Set `NO_COLOR` the same way to drop the colors from the prompt.

## Startup Files and History

With bash and zsh, `jmp shell` does not load your startup files (such as
`~/.bashrc` or `~/.zshrc`) unless the client config sets
`shell.use_profiles: true`. Command history still works: commands are saved to
`~/.bash_history` or `~/.zsh_history` (or to the file in `HISTFILE`, if it is
set in the environment), and Ctrl+R searches them.
