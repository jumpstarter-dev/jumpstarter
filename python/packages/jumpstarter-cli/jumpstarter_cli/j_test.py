import click

from jumpstarter_cli.cli_cache import serialize_click_group
from jumpstarter_cli.j import _handle_j_completion


def test_j_completion_source_zsh(capsys):
    raised = False
    try:
        _handle_j_completion("zsh_source")
    except SystemExit as e:
        raised = True
        assert e.code == 0
    assert raised, "_handle_j_completion should raise SystemExit(0)"
    captured = capsys.readouterr()
    assert "compdef" in captured.out


def test_j_completion_source_bash(capsys):
    raised = False
    try:
        _handle_j_completion("bash_source")
    except SystemExit as e:
        raised = True
        assert e.code == 0
    assert raised, "_handle_j_completion should raise SystemExit(0)"
    captured = capsys.readouterr()
    assert "complete" in captured.out.lower()


def test_j_completion_source_fish(capsys):
    raised = False
    try:
        _handle_j_completion("fish_source")
    except SystemExit as e:
        raised = True
        assert e.code == 0
    assert raised, "_handle_j_completion should raise SystemExit(0)"
    captured = capsys.readouterr()
    assert "complete" in captured.out.lower()


def test_j_completion_complete_instruction_passes_through():
    _handle_j_completion("bash_complete")


def test_j_completion_uses_cache_for_complete_instruction(monkeypatch, capsys):
    @click.group()
    def fake_cli():
        """fake"""

    @fake_cli.command()
    def power():
        """Power control"""

    @fake_cli.command()
    def storage():
        """Storage control"""

    cache = serialize_click_group(fake_cli)
    monkeypatch.setenv("_J_CLI_CACHE", cache)
    monkeypatch.setenv("_J_COMPLETE", "bash_complete")
    monkeypatch.setenv("COMP_WORDS", "j ")
    monkeypatch.setenv("COMP_CWORD", "1")

    raised = False
    try:
        _handle_j_completion("bash_complete")
    except SystemExit as e:
        raised = True
        assert e.code == 0
    assert raised
    captured = capsys.readouterr()
    assert "power" in captured.out
    assert "storage" in captured.out


def test_j_completion_falls_through_without_cache():
    _handle_j_completion("bash_complete")


def test_j_entry_point_intercepts_complete_var(monkeypatch, capsys):
    monkeypatch.setenv("_J_COMPLETE", "zsh_source")
    raised = False
    try:
        from jumpstarter_cli.j import j
        j()
    except SystemExit as e:
        raised = True
        assert e.code == 0
    assert raised
    captured = capsys.readouterr()
    assert "compdef" in captured.out
