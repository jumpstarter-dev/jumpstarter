//! Jumpstarter exporter runtime (spec doc 03; native-migration design in
//! `rust/docs/03-native-exporter-migration.md`).
//!
//! A Rust exporter that registers with the controller, consumes the `Status`/`Listen`
//! streams, and serves one lease at a time. It **serves the client/hook-facing
//! `ExporterService` + `RouterService` itself** ([`session`] + [`tunnel`]) on its own
//! main + hook sockets, terminating each client tunnel into that server, and hosts each
//! driver in its own subprocess ([`polyglot`] — one host per driver, Python or native
//! Rust) that it routes driver calls into by UUID ([`routing`]).
//!
//! Each lease is **driven** by the [`lease_fsm`] typestate state machine via the
//! [`lease_runner`] (its effects backed by [`controller_effects`]), executing the
//! `beforeLease`/`afterLease` [`hooks`] against the Rust hook socket and reporting the
//! status sequence both to the controller and the `GetStatus` snapshot via [`control`].
//!
//! Still deferred: the supervisor fork/restart loop + rapid-failure breaker, the
//! `_retry_stream` contract (5×1.0 s), and standalone TCP.

pub mod auth;
pub mod backend;
pub mod control;
pub mod controller_effects;
pub mod driver_host;
pub mod exporter;
pub mod hooks;
pub mod lease_fsm;
pub mod lease_runner;
pub mod logbuf;
pub mod polyglot;
pub mod routing;
pub mod session;
pub mod shm_backend;
pub mod standalone;
pub mod tunnel;

/// The exporter reuses the client's error taxonomy (RPC / transport / config) for
/// the shared controller-channel and router-bridge paths.
pub type Error = jumpstarter_lease::ClientError;

/// Tie a (native Rust) driver-host process's lifetime to the polyglot hub that spawned it.
///
/// The hub SIGKILLs each host on lease teardown, but if the hub itself dies *ungracefully*
/// (SIGKILL, crash, OOM) those kills never run, and the host — a native `jmp-rust-host`
/// subprocess — would orphan and keep running, piling up idle hosts across runs.
///
/// It watches the **hub's own pid** (passed as `JMP_HUB_PID`) via `kill(pid, 0)`, *not* its own
/// parent: a host can be reparented to init the instant it spawns (so a `getppid`-change check
/// never fires), but the hub's pid is stable. `kill(2)` is POSIX — one code path for Linux,
/// macOS and every BSD, no Linux-only `prctl(PR_SET_PDEATHSIG)`. It runs on a dedicated OS thread
/// so the scheduler always advances it; on the hub's death it `_exit`s immediately.
///
/// NOTE: only for *native* hosts. A Python host must do this in Python (`jumpstarter.exporter_host`)
/// — terminating a Python process from this embedded core deadlocks on interpreter finalization.
pub fn exit_when_orphaned() {
    let Some(hub_pid) = std::env::var("JMP_HUB_PID").ok().and_then(|s| s.parse::<i32>().ok()) else {
        return; // not spawned by the hub (e.g. a direct invocation) — nothing to watch.
    };
    std::thread::Builder::new()
        .name("parent-death-watch".into())
        .spawn(move || loop {
            std::thread::sleep(std::time::Duration::from_millis(250));
            // kill(pid, 0) probes liveness: -1/ESRCH => the hub is gone.
            if unsafe { libc::kill(hub_pid, 0) } != 0
                && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
            {
                unsafe { libc::_exit(0) };
            }
        })
        .expect("spawn parent-death watchdog thread");
}

pub use exporter::{run, run_with_factory, ExporterExit, RunOptions};
pub use standalone::serve_standalone_tcp;
