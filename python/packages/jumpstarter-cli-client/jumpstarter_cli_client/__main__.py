"""Allow running Jumpstarter through `python -m jumpstarter_cli_client`."""

from . import client

if __name__ == "__main__":
    client(prog_name="jmp-client")
