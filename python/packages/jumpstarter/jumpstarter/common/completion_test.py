import os
import shlex
import shutil
import subprocess

import click
import pytest

from .completion import bash_completion_script


def _bash_version() -> tuple[int, int] | None:
    bash = shutil.which("bash")
    if bash is None:
        return None
    result = subprocess.run(
        [bash, "-c", 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'], capture_output=True, text=True, check=False
    )
    major, minor = result.stdout.split()
    return int(major), int(minor)


# The completion script returns early on bash older than 4.2 (macOS ships 3.2)
requires_completion_bash = pytest.mark.skipif(
    (_bash_version() or (0, 0)) < (4, 2), reason="needs bash 4.2 or later"
)


def _sample_tree() -> click.Group:
    @click.group("base")
    @click.option("--log-level", type=click.Choice(["DEBUG", "INFO"]), help="Set the log level")
    def base():
        """Composite device"""

    @base.group("power")
    def power():
        """Power control"""

    @power.command()
    def on():
        """Power on"""

    @power.command(hidden=True)
    def enable():
        """Alias of on"""

    @base.group("storage")
    def storage():
        """Storage"""

    @storage.command()
    @click.argument("image", type=click.Path(dir_okay=False))
    @click.option("--target", "-t", type=click.Choice(["usb", "emmc"]), multiple=True)
    @click.option("--verify/--no-verify", default=True)
    @click.option("--force", is_flag=True)
    @click.option("-v", "verbose", count=True)
    @click.option("--secret", hidden=True)
    @click.option("--dir", type=click.Path(file_okay=False))
    def flash(image, target, verify, force, verbose, secret, dir):
        """Flash an image"""

    @base.command("shell", context_settings={"help_option_names": ["-h", "--help"]})
    @click.argument("args", nargs=-1)
    def shell(args):
        """Run a shell"""

    return base


def _bash(script: str, cwd) -> str:
    result = subprocess.run(
        ["bash", "--norc", "--noprofile", "-c", script],
        cwd=cwd,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout


def _complete(cli: click.Command, words: list[str], cwd) -> set[str]:
    """COMPREPLY of the completion function for the command line ``words``."""
    request = " ".join(shlex.quote(w) for w in words)
    script = bash_completion_script(cli) + (
        f'COMP_WORDS=({request}); COMP_CWORD={len(words) - 1}; _j_complete; printf "%s\\n" "${{COMPREPLY[@]}}"\n'
    )
    return set(_bash(script, cwd).splitlines()) - {""}


FLASH_OPTIONS = {"--target", "-t", "--verify", "--no-verify", "--force", "-v", "--dir", "--help"}


@requires_completion_bash
@pytest.mark.parametrize(
    ("words", "expected"),
    [
        # subcommands and options together, as a double TAB lists them
        (["j", ""], {"power", "shell", "storage", "--log-level", "--help"}),
        (["j", "p"], {"power"}),
        (["j", "-"], {"--log-level", "--help"}),
        # hidden aliases are not offered, but a command line using them is understood
        (["j", "power", ""], {"on", "--help"}),
        (["j", "power", "en"], set()),
        (["j", "power", "enable", ""], {"--help"}),
        # option values, also as --opt=value, and skipping them on the way
        (["j", "--log-level", ""], {"DEBUG", "INFO"}),
        (["j", "--log-level", "=", ""], {"DEBUG", "INFO"}),
        (["j", "--log-level", "="], {"DEBUG", "INFO"}),
        (["j", "--log-level", "I"], {"INFO"}),
        (["j", "--log-level", "DEBUG", "power", ""], {"on", "--help"}),
        (["j", "--log-level", "=", "DEBUG", "power", ""], {"on", "--help"}),
        # options of a leaf command; hidden ones are left out
        (["j", "storage", "flash", "-"], FLASH_OPTIONS),
        (["j", "storage", "flash", "-t", ""], {"usb", "emmc"}),
        (["j", "storage", "flash", "--force", "-t", "u"], {"usb"}),
        # paths: files for a file argument, directories for a directory option
        (["j", "storage", "flash", "a"], {"afile", "adir"}),
        (["j", "storage", "flash", "--dir", ""], {"adir"}),
        (["j", "storage", "flash", ""], {"afile", "adir"} | FLASH_OPTIONS),
        (["j", "storage", "flash", "-t", "usb", "a"], {"afile", "adir"}),
        # free text arguments also offer files; per-command help option names
        (["j", "shell", ""], {"afile", "adir", "-h", "--help"}),
        # options already given are not offered again, unless they repeat
        (["j", "power", "on", "--help", ""], set()),
        (["j", "storage", "flash", "--force", "--v"], {"--verify"}),
        (["j", "storage", "flash", "--force", "-v", "-t", "usb", "-"], FLASH_OPTIONS - {"--force"}),
        (["j", "--help", "storage", "flash", "--h"], {"--help"}),
        # nothing to offer: bash falls back to filenames (complete -o default)
        (["j", "power", "on", "x"], set()),
    ],
)
def test_bash_completion(tmp_path, words, expected):
    (tmp_path / "afile").touch()
    (tmp_path / "adir").mkdir()
    assert _complete(_sample_tree(), words, tmp_path) == expected


@requires_completion_bash
def test_bash_completion_falls_back_to_filenames(tmp_path):
    script = bash_completion_script(_sample_tree()) + "complete -p j\n"
    assert _bash(script, tmp_path) == "complete -o default -F _j_complete j\n"


def _mode_command(choices: list[str]) -> click.Group:
    @click.group("base")
    def base():
        pass

    @base.command("it's")
    @click.option("--mode", type=click.Choice(choices))
    def cmd(mode):
        pass

    return base


@requires_completion_bash
def test_bash_completion_runs_no_value_from_the_drivers(tmp_path):
    """Names and choices come from drivers: they are data, never code.

    Choices are offered quoted, so they are not expanded when the completed
    command runs either.
    """
    (tmp_path / "afile").touch()
    tree = _mode_command(["a$(touch PWNED)", "`touch PWNED`", "*"])
    assert _complete(tree, ["j", "it's", "--mode", ""], tmp_path) == {
        r"a\$\(touch\ PWNED\)",
        r"\`touch\ PWNED\`",
        r"\*",
    }
    assert not (tmp_path / "PWNED").exists()


@requires_completion_bash
@pytest.mark.parametrize(
    ("cur", "expected"),
    [("", {r"fast\ mode", "slow"}), ("fa", {r"fast\ mode"}), ("fast ", {r"fast\ mode"})],
)
def test_bash_completion_keeps_choices_with_spaces_whole(tmp_path, cur, expected):
    tree = _mode_command(["fast mode", "slow"])
    assert _complete(tree, ["j", "it's", "--mode", cur], tmp_path) == expected


@requires_completion_bash
def test_bash_completion_quotes_a_choice_so_bash_reads_it_back(tmp_path):
    """What completion inserts is what the command receives."""
    tree = _mode_command(["fast mode", "a$(touch PWNED)"])
    script = bash_completion_script(tree) + (
        "COMP_WORDS=(j \"it's\" --mode ''); COMP_CWORD=3; _j_complete\n"
        'for reply in "${COMPREPLY[@]}"; do eval "printf \'[%s]\\n\' $reply"; done\n'
    )
    assert set(_bash(script, tmp_path).splitlines()) == {"[fast mode]", "[a$(touch PWNED)]"}
    assert not (tmp_path / "PWNED").exists()


@requires_completion_bash
def test_bash_completion_leaves_out_a_driver_that_fails(tmp_path):
    class Broken(click.Group):
        def list_commands(self, ctx):
            return ["bad", "good"]

        def get_command(self, ctx, cmd_name):
            if cmd_name == "bad":
                raise RuntimeError("driver bug")
            return click.Command(cmd_name)

    tree = Broken("base")
    assert _complete(tree, ["j", ""], tmp_path) == {"good", "--help"}


def test_bash_completion_returns_early_on_old_bash():
    """Nothing bash 4.2 introduced may run before the version check.

    bash older than 4.2, such as macOS's 3.2, cannot run in CI; the check must
    come first so that on those versions the script defines nothing.
    """
    script = bash_completion_script(_sample_tree())
    guard = script.index("(( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 2) )) || return 0")
    before = script[:guard]
    assert all(line.startswith("#") or not line.strip() for line in before.splitlines())


@requires_completion_bash
def test_bash_completion_skips_runtime_callbacks(tmp_path):
    @click.command("cmd")
    @click.argument("name", type=click.Choice(["x"]), shell_complete=lambda ctx, param, incomplete: ["dynamic"])
    def cmd(name):
        pass

    assert _complete(cmd, ["j", ""], tmp_path) == {"x", "--help"}
