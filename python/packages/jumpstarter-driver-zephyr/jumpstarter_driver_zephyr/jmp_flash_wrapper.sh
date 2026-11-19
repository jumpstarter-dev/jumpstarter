#!/bin/bash
# Flash entry point for twister's --flash-command.
#
# Twister appends `--build-dir <dir>` (and `--board-id <id>` when known) after
# the command name, so it can't call `j zephyr flash` directly — those flags
# would land on `j`. This wrapper picks out --build-dir and forwards it to the
# driver's flash subcommand, attached to the surrounding `jmp shell` lease.
# --driver names the Zephyr driver in the exporter (default: zephyr);
# `j <driver> twister` passes it after twister's own flags.
# --board-id is ignored: the lease already pins the board.

build_dir=
driver=zephyr

while [ $# -gt 0 ]; do
    case "$1" in
        --build-dir)
            build_dir="$2"
            shift 2
            ;;
        --driver)
            driver="$2"
            shift 2
            ;;
        *)
            shift  # Skip unknown arguments (e.g. --board-id)
            ;;
    esac
done

j "$driver" flash --build-dir "$build_dir"
