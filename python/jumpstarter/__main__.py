"""Allow running Jumpstarter through `python -m jumpstarter`."""

from jumpstarter.cli.client import client

if __name__ == "__main__":
    client(prog_name="jmp")
