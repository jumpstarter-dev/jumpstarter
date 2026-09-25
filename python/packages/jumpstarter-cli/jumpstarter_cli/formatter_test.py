import click
from click.testing import CliRunner

from jumpstarter_cli.formatter import RSTStrippingCommand, _rst_to_click


def test_rst_to_click_strips_directive_line():
    text = "Example:\n\n.. code-block:: bash\n\n    $ jmp foo"
    result = _rst_to_click(text)
    assert ".. code-block::" not in result


def test_rst_to_click_inserts_verbatim_marker():
    text = "Example:\n\n.. code-block:: bash\n\n    $ jmp foo"
    result = _rst_to_click(text)
    assert "\b" in result


def test_rst_to_click_preserves_non_directive_text():
    text = "Run a shell.\n\nExample:\n\n.. code-block:: bash\n\n    $ jmp foo"
    result = _rst_to_click(text)
    assert "Run a shell." in result
    assert "Example:" in result
    assert "$ jmp foo" in result


def test_rst_to_click_multiple_blocks():
    text = (
        "First:\n\n.. code-block:: bash\n\n    $ jmp a\n\n"
        "Second:\n\n.. code-block:: bash\n\n    $ jmp b"
    )
    result = _rst_to_click(text)
    assert ".. code-block::" not in result
    assert result.count("\b") == 2
    assert "$ jmp a" in result
    assert "$ jmp b" in result


def test_rst_stripping_command_help_hides_directives():
    @click.command(cls=RSTStrippingCommand)
    def cmd():
        """Summary.

        Example:

        .. code-block:: bash

            $ jmp shell --exporter foo
        """

    runner = CliRunner()
    result = runner.invoke(cmd, ["--help"])
    assert result.exit_code == 0
    assert ".. code-block::" not in result.output
    assert "$ jmp shell --exporter foo" in result.output


def test_rst_stripping_command_original_help_preserves_rst():
    @click.command(cls=RSTStrippingCommand)
    def cmd():
        """Summary.

        .. code-block:: bash

            $ jmp shell --exporter foo
        """

    assert ".. code-block:: bash" in cmd.help
