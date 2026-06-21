//! `jmp run` — serve an exporter (spec 08 §9.3; `run.py`). Controller-mediated by
//! default, or a standalone TCP listener with `--tls-grpc-listener`. The fork/restart
//! supervisor and TLS-cert listener mode are deferred.

use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;

use clap::Args as ClapArgs;
use jumpstarter_config::{paths, ExporterConfig, YamlConfig};
use jumpstarter_exporter::RunOptions;

#[derive(ClapArgs)]
pub struct Args {
    /// Exporter config alias (resolved from the user dir, then /etc/jumpstarter/exporters).
    #[arg(long)]
    exporter: Option<String>,
    /// Path to an exporter config file.
    #[arg(long = "exporter-config")]
    exporter_config: Option<PathBuf>,
    /// Listen on TCP instead of registering with a controller. E.g. 1234 or 0.0.0.0:1234.
    #[arg(long = "tls-grpc-listener", value_name = "[HOST:]PORT")]
    tls_grpc_listener: Option<String>,
    /// With --tls-grpc-listener, listen without TLS (insecure, development only).
    #[arg(long = "tls-grpc-insecure")]
    tls_grpc_insecure: bool,
    /// Server certificate (PEM) for --tls-grpc-listener (TLS mode; not yet supported).
    #[arg(long = "tls-cert")]
    tls_cert: Option<PathBuf>,
    /// Server private key (PEM) for --tls-grpc-listener (TLS mode; not yet supported).
    #[arg(long = "tls-key")]
    tls_key: Option<PathBuf>,
    /// Require this passphrase from clients connecting via --tls-grpc-listener.
    #[arg(long)]
    passphrase: Option<String>,
}

pub async fn run(args: Args) -> u8 {
    match run_impl(args).await {
        Ok(()) => 0,
        Err((code, message)) => {
            eprintln!("Error: {message}");
            code
        }
    }
}

async fn run_impl(args: Args) -> Result<(), (u8, String)> {
    let usage = |m: &str| Err((2u8, m.to_string()));

    // Resolve the exporter config path from --exporter alias or --exporter-config path.
    let path = match (&args.exporter, &args.exporter_config) {
        (Some(_), Some(_)) => {
            return usage("only one of --exporter, --exporter-config should be specified")
        }
        (Some(alias), None) => paths::resolve_exporter_path(alias),
        (None, Some(p)) => p.clone(),
        (None, None) => return usage("one of --exporter or --exporter-config is required"),
    };

    let standalone = args.tls_grpc_listener.is_some();
    if !standalone
        && (args.tls_grpc_insecure
            || args.tls_cert.is_some()
            || args.tls_key.is_some()
            || args.passphrase.is_some())
    {
        return usage(
            "--tls-grpc-insecure, --tls-cert, --tls-key, and --passphrase require --tls-grpc-listener",
        );
    }

    if let Some(bind) = &args.tls_grpc_listener {
        let addr = parse_bind(bind).map_err(|m| (2u8, m))?;
        if args.tls_grpc_insecure && (args.tls_cert.is_some() || args.tls_key.is_some()) {
            return usage("--tls-grpc-insecure cannot be combined with --tls-cert / --tls-key");
        }
        if !args.tls_grpc_insecure {
            // TLS-cert listener mode is not yet ported.
            return usage(
                "--tls-grpc-listener currently requires --tls-grpc-insecure (TLS-cert mode not yet supported)",
            );
        }
        // The Rust-native `jmp run` hosts drivers via the slim subprocess host (the
        // in-process FFI host belongs to the Python entrypoint).
        let factory = std::sync::Arc::new(jumpstarter_exporter::backend::SlimHostFactory::new(
            path.clone(),
        ));
        return jumpstarter_exporter::serve_standalone_tcp(&path, addr, args.passphrase, factory)
            .await
            .map_err(|e| (1u8, e.to_string()));
    }

    // Controller-mediated mode.
    let config = ExporterConfig::load(&path).map_err(|e| {
        (
            1u8,
            format!("cannot load exporter '{}': {e}", path.display()),
        )
    })?;
    jumpstarter_exporter::run(RunOptions {
        config,
        config_path: path,
    })
    .await
    .map_err(|e| (1u8, e.to_string()))
}

/// Parse `[HOST:]PORT` into a socket address; the default host is `0.0.0.0`
/// (`run.py:_parse_listener_bind`).
fn parse_bind(value: &str) -> Result<SocketAddr, String> {
    let (host, port_str) = match value.rsplit_once(':') {
        Some((h, p)) => {
            let h = h.trim();
            (if h.is_empty() { "0.0.0.0" } else { h }, p)
        }
        None => ("0.0.0.0", value),
    };
    let port: u16 = port_str
        .parse()
        .map_err(|_| format!("port must be an integer, got '{port_str}'"))?;
    if port == 0 {
        return Err(format!("port must be between 1 and 65535, got {port}"));
    }
    let ip: IpAddr = host
        .parse()
        .map_err(|_| format!("invalid listener host '{host}'"))?;
    Ok(SocketAddr::new(ip, port))
}
