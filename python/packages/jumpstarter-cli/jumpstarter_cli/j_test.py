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
