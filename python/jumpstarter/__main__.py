"""Allow running Jumpstarter through `python -m jumpstarter`."""

import anyio


def main():
    anyio.run(anyio.to_thread.run_sync, entrypoint)


def entrypoint():
    from jumpstarter.cli import main

    main(prog_name="jmp")

    # FIXME: Error in sys.excepthook


if __name__ == "__main__":
    main()
