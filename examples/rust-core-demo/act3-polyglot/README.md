# Act 3 — "Everything at once": one exporter, Python + Rust + Java drivers

**Story:** A single exporter hosts three drivers written in **three languages** — a Python
`MockPower`, a native **Rust** driver, and a **Java/Kotlin** driver — and the native (Rust) `jmp`
/ `j` client drives all of them through one lease. Each driver runs in its own host subprocess
(its own runtime/GIL), federated by UUID through the Rust hub, every call a native per-interface
gRPC over the unified Rust transport.

Then the same three drivers are driven **from a native Rust test** —
[`power_test.rs`](power_test.rs), right here in this directory — using the build-time-generated
typed `PowerClient`. With Act 1 (pytest) and Act 2 (JUnit/Kotlin) that completes the trilogy:
the same interface, the same transport, a native test in every language. (The crate
`jumpstarter-driver-power-pure-client` compiles this file via a `#[path]` shim, so the file you
read here is the exact code that runs.)

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

Two terminals — the scripts carry the full host wiring and the exact `j` commands:

```bash
./serve.sh   # terminal A: spawn the 3 per-language driver hosts + register the exporter
./run.sh     # terminal B: one lease; j pypower/rustpower/jvmpower, then the Rust test power_test.rs
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
  and the Rust hub/transport. (Swap `rustpower` to `type: rust:jumpstarter-driver-power-pure` for a
  genuinely native Rust typed client, if you want that flourish and have built its client binary.)
- Per-entry `host:` in the exporter config can pin any entry to a specific host binary if the
  env-based resolution above isn't convenient.
