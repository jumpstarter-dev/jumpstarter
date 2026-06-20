//! `jmp mcp serve` — run the Jumpstarter MCP server over stdio.
//!
//! Replaces the Python `jumpstarter-mcp` package. Controller/lease/connection tools run on
//! the Rust core; run/introspection tools shell out to the Python `j` CLI. Meant to be
//! launched by an MCP host (e.g. Cursor) as a subprocess; all I/O is JSON-RPC over stdio.

use clap::{Args as ClapArgs, Subcommand};

use crate::cmderr::runtime;

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: McpCommand,
}

#[derive(Subcommand)]
enum McpCommand {
    /// Start the MCP server with stdio transport.
    Serve,
}

pub async fn run(args: Args) -> u8 {
    match args.command {
        McpCommand::Serve => match jumpstarter_mcp::run_server().await {
            Ok(()) => 0,
            Err(e) => runtime(e).report(),
        },
    }
}
