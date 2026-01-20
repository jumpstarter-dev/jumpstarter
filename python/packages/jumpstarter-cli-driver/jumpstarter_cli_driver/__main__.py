"""Allow running Jumpstarter through `python -m jumpstarter_cli_driver`."""

from . import driver

if __name__ == "__main__":
    driver(prog_name="jmp-driver")
