import click

from jumpstarter_cli.cli_cache import deserialize_click_group, serialize_click_group


def _build_sample_cli():
    @click.group()
    @click.option(
        "--log-level",
        type=click.Choice(["DEBUG", "INFO", "WARNING"]),
    )
    def root(log_level):
        """Root group"""

    @root.group()
    def power():
        """Power interface"""

    @power.command()
    def on():
        """Turn on"""

    @power.command()
    def off():
        """Turn off"""

    @root.command()
    @click.argument("args", nargs=-1)
    def ssh(args):
        """SSH into device"""

    return root


def test_round_trip_preserves_group_names():
    cli = _build_sample_cli()
    data = serialize_click_group(cli)
    restored = deserialize_click_group(data)

    assert set(restored.list_commands(None)) == {"power", "ssh"}


def test_round_trip_preserves_subcommands():
    cli = _build_sample_cli()
    data = serialize_click_group(cli)
    restored = deserialize_click_group(data)

    power = restored.get_command(None, "power")
    assert isinstance(power, click.Group)
    assert set(power.list_commands(None)) == {"on", "off"}


def test_round_trip_preserves_help_text():
    cli = _build_sample_cli()
    data = serialize_click_group(cli)
    restored = deserialize_click_group(data)

    assert restored.help == "Root group"
    power = restored.get_command(None, "power")
    assert power.help == "Power interface"


def test_round_trip_preserves_options():
    cli = _build_sample_cli()
    data = serialize_click_group(cli)
    restored = deserialize_click_group(data)

    option_names = [p.name for p in restored.params if isinstance(p, click.Option)]
    assert "log_level" in option_names


def test_round_trip_preserves_choice_type():
    cli = _build_sample_cli()
    data = serialize_click_group(cli)
    restored = deserialize_click_group(data)

    for p in restored.params:
        if isinstance(p, click.Option) and p.name == "log_level":
            assert isinstance(p.type, click.Choice)
            assert list(p.type.choices) == ["DEBUG", "INFO", "WARNING"]
            break
    else:
        raise AssertionError("log_level option not found")


def test_round_trip_preserves_arguments():
    cli = _build_sample_cli()
    data = serialize_click_group(cli)
    restored = deserialize_click_group(data)

    ssh = restored.get_command(None, "ssh")
    args = [p for p in ssh.params if isinstance(p, click.Argument)]
    assert len(args) == 1
    assert args[0].nargs == -1
