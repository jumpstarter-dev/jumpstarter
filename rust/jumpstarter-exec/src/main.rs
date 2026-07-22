use std::env;
use std::process;

use jumpstarter_exec::{client, server};

const DEFAULT_SOCKET: &str = "/shared/launcher.sock";

fn main() {
    let args: Vec<String> = env::args().collect();

    if args.len() < 2 {
        usage();
        process::exit(1);
    }

    match args[1].as_str() {
        "serve" => {
            let socket =
                parse_option(&args[2..], "--socket").unwrap_or_else(|| DEFAULT_SOCKET.to_string());
            if let Err(e) = server::serve(&socket) {
                eprintln!("jumpstarter-exec serve: {e}");
                process::exit(1);
            }
        }
        "exec" => {
            let socket =
                parse_option(&args[2..], "--socket").unwrap_or_else(|| DEFAULT_SOCKET.to_string());

            let separator = args.iter().position(|a| a == "--").unwrap_or_else(|| {
                eprintln!("Usage: jumpstarter-exec exec [--socket <path>] -- <command> [args...]");
                process::exit(1);
            });

            let argv: Vec<String> = args[separator + 1..].to_vec();
            if argv.is_empty() {
                eprintln!("jumpstarter-exec exec: no command specified after '--'");
                process::exit(1);
            }

            match client::exec(&socket, argv) {
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
    eprintln!("Usage: jumpstarter-exec <serve|exec> [options]");
    eprintln!();
    eprintln!("Subcommands:");
    eprintln!("  serve [--socket <path>]                 Listen for exec requests");
    eprintln!("  exec  [--socket <path>] -- <cmd> [...]  Execute a command remotely");
}

/// Parse a `--flag value` pair from a slice of arguments.
fn parse_option(args: &[String], flag: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == flag).map(|w| w[1].clone())
}
