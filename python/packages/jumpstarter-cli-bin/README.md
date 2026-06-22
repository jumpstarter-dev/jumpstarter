# jumpstarter-cli-bin

The native Rust `jmp` CLI binary, packaged as a platform wheel via
[maturin](https://www.maturin.rs/) (`bindings = "bin"`).

`pip install jumpstarter-cli-bin` drops a standalone `jmp` executable into the environment's
`bin/` — the same Rust binary built from `rust/jumpstarter-cli`, with no Python needed to launch
it. `jumpstarter-cli` depends on this package so `jmp` is the native binary, while `j` (the
Python driver-client CLI) stays a Python entrypoint.

`jmp run` is the polyglot exporter hub: it spawns one driver-host subprocess per top-level
`export:` entry (Python via `python -m jumpstarter.exporter_host`, native Rust via
`jmp-rust-host`), so a pure-native driver set needs no Python at all.
