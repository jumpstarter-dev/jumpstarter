from click.testing import CliRunner

from .jmp import jmp


def test_cli():
    runner = CliRunner()
    result = runner.invoke(jmp, [])
    # shell/create/delete/update/get/auth run on the Rust core (forwarded via FFI); their
    # flags are validated by the Rust CLI. config/login/run/version stay native Python.
    for subcommand in [
        "auth",
        "config",
        "create",
        "delete",
        "driver",
        "get",
        "login",
        "run",
        "shell",
        "update",
        "version",
    ]:
        assert subcommand in result.output
