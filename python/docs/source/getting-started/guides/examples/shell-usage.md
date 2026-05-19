# Shell Usage

## Starting and Exiting a Session

Start a {term}`local mode` exporter {term}`session`:
```console
$ jmp shell --exporter example-local
```

Start a {term}`distributed mode` exporter session:
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
  --help  Show this message and exit.

Commands:
  power    Generic power
  storage  Generic storage mux
$ j power on
ok
$ j power off
ok
$ exit
```

When you run the `j` command in the exporter shell, you're accessing the CLI
interfaces exposed by the drivers configured in your exporter. In this example:

- `j power` - Would access the power interface from the MockPower driver
- `j storage` - Would access the storage interface from the MockStorageMux
  driver

Each driver can expose different commands through this interface, making it easy
to interact with the mock hardware. The command structure follows `j
<driver_type> <action>`, where available actions depend on the specific driver.
