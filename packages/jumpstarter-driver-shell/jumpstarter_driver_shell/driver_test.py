import pytest

from .driver import Shell
from jumpstarter.common.utils import serve


@pytest.fixture
def client():
    instance = Shell(
        log_level="DEBUG",
        methods={
            "echo": "echo $1",
            "env": "echo $ENV1",
            "multi_line": "echo $1\necho $2\necho $3",
            "exit1": "exit 1",
            "stderr": "echo $1 >&2",
        },
    )
    with serve(instance) as client:
        yield client


def test_normal_args(client):
    assert client.echo("hello") == ("hello\n", "", 0)


def test_env_vars(client):
    assert client.env(ENV1="world") == ("world\n", "", 0)


def test_multi_line_scripts(client):
    assert client.multi_line("a", "b", "c") == ("a\nb\nc\n", "", 0)


def test_return_codes(client):
    assert client.exit1() == ("", "", 1)


def test_stderr(client):
    assert client.stderr("error") == ("", "error\n", 0)


def test_unknown_method(client):
    try:
        client.unknown()
    except AttributeError as e:
        assert "method unknown not found in" in str(e)
    else:
        raise AssertionError("Expected AttributeError")
