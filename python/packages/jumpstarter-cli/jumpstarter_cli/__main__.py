"""Allow running Jumpstarter through `python -m jumpstarter_cli`."""

from . import jmp

if __name__ == "__main__":
    jmp(prog_name="jmp")
