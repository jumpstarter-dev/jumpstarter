"""Allow running Jumpstarter through `python -m jumpstarter_cli_admin`."""

from . import admin

if __name__ == "__main__":
    admin(prog_name="jmp-admin")
