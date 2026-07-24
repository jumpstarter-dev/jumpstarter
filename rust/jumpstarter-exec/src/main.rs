use std::collections::BTreeMap;
use std::env;
use std::process;

use jumpstarter_exec::log::{parse_log_fields, LogFormat};
use jumpstarter_exec::server::{self, ServeOptions};

const DEFAULT_SOCKET: &str = "/shared/launcher.sock";

/// Git-derived version, embedded at compile time by `build.rs`.
const VERSION: &str = env!("GIT_VERSION");

fn main() {
    let args: Vec<String> = env::args().collect();

    if args.len() < 2 {
        usage();
        process::exit(1);
    }

    match args[1].as_str() {
        "version" => {
            println!("jumpstarter-exec {VERSION}");
        }
        "serve" => {
            let rest = &args[2..];
            let socket =
                parse_option(rest, "--socket").unwrap_or_else(|| DEFAULT_SOCKET.to_string());
            let debug = flag_present(rest, "--debug") || env_truthy("JUMPSTARTER_EXEC_DEBUG");
            let log_format = parse_option(rest, "--log-format")
                .and_then(|s| LogFormat::parse(&s))
                .unwrap_or(LogFormat::Json);

            let log_fields = match collect_log_fields(rest) {
                Ok(f) => f,
                Err(e) => {
                    eprintln!("jumpstarter-exec serve: {e}");
                    process::exit(1);
                }
            };

            let opts = ServeOptions {
                debug,
                log_format,
                log_fields,
            };
            if let Err(e) = server::serve_with(&socket, opts) {
                eprintln!("jumpstarter-exec serve: {e}");
                process::exit(1);
            }
        }
        "exec" => {
            let rest = &args[2..];
            let socket =
                parse_option(rest, "--socket").unwrap_or_else(|| DEFAULT_SOCKET.to_string());

            let separator = args.iter().position(|a| a == "--").unwrap_or_else(|| {
                eprintln!("Usage: jumpstarter-exec exec [--socket <path>] -- <command> [args...]");
                process::exit(1);
            });

            let argv: Vec<String> = args[separator + 1..].to_vec();
            if argv.is_empty() {
                eprintln!("jumpstarter-exec exec: no command specified after '--'");
                process::exit(1);
            }

            match jumpstarter_exec::client::exec(&socket, argv) {
                Ok(code) => process::exit(code),
                Err(e) => {
                    eprintln!("jumpstarter-exec exec: {e}");
                    process::exit(1);
                }
            }
        }
        other => {
            eprintln!("jumpstarter-exec: unknown subcommand '{other}'");
            usage();
            process::exit(1);
        }
    }
}

fn usage() {
    eprintln!("Usage: jumpstarter-exec <serve|exec|version> [options]");
    eprintln!();
    eprintln!("Subcommands:");
    eprintln!("  serve   [--socket <path>] [--debug] [--log-format json|text]");
    eprintln!("          [--log-field key=value]...");
    eprintln!("          Listen for exec requests (JSON logs by default)");
    eprintln!("  exec    [--socket <path>] -- <cmd> [...]");
    eprintln!("          Execute a command remotely");
    eprintln!("  version  Print version and exit");
    eprintln!();
    eprintln!("Log context (JEP-0013 persistent fields on every line):");
    eprintln!("  --log-field key=value   (repeatable), e.g. --log-field exporter=demo-abc");
    eprintln!("  JUMPSTARTER_EXEC_LOG_FIELDS=key=value,key=value");
    eprintln!("  Common keys: component, exporter, namespace");
    eprintln!();
    eprintln!("Debug: --debug or JUMPSTARTER_EXEC_DEBUG=1 logs argv and I/O previews");
}

/// Merge log fields from env and repeatable `--log-field` flags.
/// CLI flags override env on key collision.
fn collect_log_fields(args: &[String]) -> Result<BTreeMap<String, String>, String> {
    let mut fields = BTreeMap::new();
    if let Ok(raw) = env::var("JUMPSTARTER_EXEC_LOG_FIELDS") {
        fields.extend(parse_log_fields(&raw)?);
    }
    for pair in parse_all_options(args, "--log-field") {
        let parsed = parse_log_fields(&pair)?;
        fields.extend(parsed);
    }
    Ok(fields)
}

/// Parse a `--flag value` pair from a slice of arguments.
fn parse_option(args: &[String], flag: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == flag).map(|w| w[1].clone())
}

/// Collect every `--flag value` occurrence.
fn parse_all_options(args: &[String], flag: &str) -> Vec<String> {
    args.windows(2)
        .filter(|w| w[0] == flag)
        .map(|w| w[1].clone())
        .collect()
}

fn flag_present(args: &[String], flag: &str) -> bool {
    args.iter().any(|a| a == flag)
}

fn env_truthy(name: &str) -> bool {
    match env::var(name) {
        Ok(v) => matches!(v.as_str(), "1" | "true" | "TRUE" | "yes" | "YES"),
        Err(_) => false,
    }
}
