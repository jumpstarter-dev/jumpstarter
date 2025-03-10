"""Allow running Jumpstarter through `python -m jumpstarter_cli_exporter`."""

from . import exporter

if __name__ == "__main__":
    exporter(prog_name="jmp-exporter")
