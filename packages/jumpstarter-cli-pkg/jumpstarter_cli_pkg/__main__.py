"""Allow running Jumpstarter through `python -m jumpstarter_cli_pkg`."""

from . import pkg

if __name__ == "__main__":
    pkg(prog_name="jmp-pkg")
