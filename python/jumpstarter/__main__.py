"""Allow running Jumpstarter through `python -m jumpstarter`."""

import anyio


def main():
    from jumpstarter.cli import main

    main(prog_name="jmp")

    # FIXME: Error in sys.excepthook


if __name__ == "__main__":
    anyio.run(anyio.to_thread.run_sync, main)
