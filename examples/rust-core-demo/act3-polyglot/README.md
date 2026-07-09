# Act 3 — "Everything at once": one exporter, Python + Rust + Java drivers

**Story:** A single exporter hosts three drivers written in **three languages** — a Python
`MockPower`, a native **Rust** driver, and a **Java/Kotlin** driver — and the native (Rust) `jmp`
/ `j` client drives all of them through one lease. Each driver runs in its own host subprocess
(its own runtime/GIL), federated by UUID through the Rust hub, every call a native per-interface
gRPC over the unified Rust transport.

This is the payoff of the interface-first, transport-in-Rust design: the language a driver is
written in is just a hosting detail.

## Prereqs

- `cluster/up.sh` has been run (`demo-polyglot` created).
- Build the per-language hosts (Rust `jmp-rust-host` + JVM `installDist`):

  ```bash
  bash examples/rust-core-demo/act3-polyglot/build-hosts.sh
  ```

  It prints the exact `export ...` block (host paths) to paste into Terminal A.

## Run

**Terminal A — host the polyglot exporter** (paste the exports printed by `build-hosts.sh`):

```bash
export JMP_DRIVER_HOST_PYTHON="$PWD/python/.venv/bin/python"
export JMP_RUST_DRIVER_HOST="$PWD/rust/target/debug/jmp-rust-host"
export JMP_JVM_DRIVER_HOST="$(ls "$PWD"/java/jumpstarter-driver-power-example/build/install/*/bin/jumpstarter-exporter-host | head -1)"
export JMP_DRIVERS_ALLOW=UNSAFE
jmp run --exporter demo-polyglot
```

**Terminal B — lease it and drive all three from the Rust client:**

```bash
jmp shell --client demo-client --selector example.com/dut=polyglot -- sh -c '
  echo "== report ==" && j &&
  echo "== python driver ==" && j pypower on &&
  echo "== rust driver =="   && j rustpower on &&
  echo "== java driver =="   && j jvmpower on'
```

Or interactively:

```bash
jmp shell --client demo-client --selector example.com/dut=polyglot
# then, inside the shell:
j pypower on
j rustpower on
j jvmpower on
```

`jmp get exporter demo-polyglot --devices` (or `j` with no args) shows the three leaves — one per
language — under a single exporter.

## What to say

- "One exporter. Three drivers, in three languages, each in its own process. One lease, one Rust
  client, one transport. The hub federates them by UUID — the caller can't tell which language is
  on the other end, because every call is the same native gRPC."

## Fallback (rehearse this)

The Python+Rust pairing is covered by an in-tree test (`rust/jumpstarter-exporter/tests/polyglot_mixed.rs`).
The **JVM** entry is coded but not covered by that mixed test — it's the riskiest live piece. If
`jvmpower` misbehaves, drop it from `exporter.yaml` (or its `jmp run`) and demo the proven
**Python + Rust** pair; show the Java driver separately via Act 2. To reset just re-run
`cluster/up.sh` (it rewrites `demo-polyglot.yaml`) or edit `/etc/jumpstarter/exporters/demo-polyglot.yaml`.

## Notes

- `rust:power` is served by `jmp-rust-host` (from `jumpstarter-driver-example`). All three drivers
  advertise the Python `PowerClient` as their client, so `j <name> on` renders the typed client via
  Python while the **driver** runs in its native language — the point being the polyglot *drivers*
  + Rust hub/transport. (Swap `rustpower` to `type: rust:jumpstarter-driver-power-pure` for a
  genuinely native Rust typed client, if you want that flourish and have built its client binary.)
- Per-entry `host:` in the exporter config can pin any entry to a specific host binary if the
  env-based resolution above isn't convenient.
