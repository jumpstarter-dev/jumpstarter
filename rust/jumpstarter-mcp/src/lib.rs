//! Rust MCP server exposing Jumpstarter hardware-management tools over stdio.
//!
//! Replaces the Python `jumpstarter-mcp` package. The controller/lease tools run on the
//! Rust core (`jumpstarter_client::ControllerSession`); the connection/run/introspection
//! tools (added in later phases) manage leases natively and shell out to the Python `j`
//! CLI for driver-client work (the driver clients are Python).

use rmcp::{
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::{CallToolResult, Content, Implementation, ProtocolVersion, ServerCapabilities, ServerInfo},
    schemars, tool, tool_handler, tool_router,
    transport::stdio,
    ErrorData as McpError, ServerHandler, ServiceExt,
};
use serde::Deserialize;
use serde_json::{json, Value};

use std::process::Stdio;
use std::time::Duration;

mod config;
mod connections;
mod shape;

const SERVER_INSTRUCTIONS: &str = "\
Jumpstarter provides remote access to physical hardware devices through a controller that \
manages leases and exporters.

Typical workflow:
1. jmp_list_leases to see existing leases, or jmp_create_lease to get a new one
2. jmp_connect with the lease ID to establish a persistent connection
3. jmp_explore to discover what CLI commands are available for this device
4. jmp_run to execute commands (power control, SSH, serial, storage, etc.)
5. jmp_disconnect and jmp_delete_lease when done

Each device type exposes different commands. Always explore before assuming what's available.
Connections are persistent - create once, run many commands against it.";

fn default_true() -> bool {
    true
}
fn default_duration() -> u64 {
    1800
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct ListExportersArgs {
    /// Optional label selector to filter exporters (e.g. "target=qemu").
    #[serde(default)]
    pub selector: Option<String>,
    /// Include current lease info for each exporter.
    #[serde(default = "default_true")]
    pub include_leases: bool,
    /// Include online/offline status.
    #[serde(default = "default_true")]
    pub include_online: bool,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct ListLeasesArgs {
    /// Optional label selector to filter leases.
    #[serde(default)]
    pub selector: Option<String>,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct CreateLeaseArgs {
    /// Lease duration in seconds (default 1800 = 30 minutes).
    #[serde(default = "default_duration")]
    pub duration_seconds: u64,
    /// Label selector to match exporters (e.g. "board=qemu").
    #[serde(default)]
    pub selector: Option<String>,
    /// Specific exporter name to lease.
    #[serde(default)]
    pub exporter_name: Option<String>,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct DeleteLeaseArgs {
    /// Name of the lease to delete.
    pub lease_id: String,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct ConnectArgs {
    /// Existing lease name to connect to.
    #[serde(default)]
    pub lease_id: Option<String>,
    /// Label selector to create a new lease (e.g. "board=qemu").
    #[serde(default)]
    pub selector: Option<String>,
    /// Specific exporter name to create a new lease.
    #[serde(default)]
    pub exporter_name: Option<String>,
    /// Lease duration in seconds (default 1800 = 30 minutes).
    #[serde(default = "default_duration")]
    pub duration_seconds: u64,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct ConnectionIdArgs {
    /// ID of the active connection.
    pub connection_id: String,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct RunArgs {
    /// ID of the active connection.
    pub connection_id: String,
    /// Command parts as a list (e.g. ["power", "on"] or ["ssh", "--", "uname", "-a"]).
    pub command: Vec<String>,
    /// Maximum execution time in seconds (default 120). Use a short value for streaming
    /// commands like "serial pipe".
    #[serde(default = "default_timeout")]
    pub timeout_seconds: u64,
}

fn default_timeout() -> u64 {
    120
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct ExploreArgs {
    /// ID of the active connection.
    pub connection_id: String,
    /// Optional path to drill into (e.g. ["storage"] to see storage subcommands).
    #[serde(default)]
    pub command_path: Option<Vec<String>>,
}

#[derive(Debug, Deserialize, schemars::JsonSchema)]
pub struct DriverMethodsArgs {
    /// ID of the active connection.
    pub connection_id: String,
    /// Path to the driver in the children tree (e.g. ["power"] or ["storage"]).
    pub driver_path: Vec<String>,
}

fn internal(msg: impl std::fmt::Display) -> McpError {
    McpError::internal_error(msg.to_string(), None)
}

/// Render a JSON value as a pretty-printed text tool result.
fn json_result(value: Value) -> Result<CallToolResult, McpError> {
    let text = serde_json::to_string_pretty(&value).map_err(internal)?;
    Ok(CallToolResult::success(vec![Content::text(text)]))
}

fn parse_array(s: &str) -> Result<Vec<Value>, McpError> {
    serde_json::from_str(s).map_err(internal)
}

/// Spawn `j <command>` against a connection, capturing stdout/stderr/exit. Drains the pipes
/// (preserving partial output) and kills the child on timeout (mirrors the Python `run_command`).
async fn run_j(
    env: &connections::ConnEnv,
    command: &[String],
    timeout_seconds: u64,
) -> Result<CallToolResult, McpError> {
    use tokio::io::AsyncReadExt;

    let j_path = match which::which("j") {
        Ok(p) => p,
        Err(_) => return json_result(json!({"error": "j CLI binary not found in PATH"})),
    };
    tracing::info!(
        command = ?command,
        timeout_seconds,
        socket = %env.socket_path,
        "run_j spawn"
    );
    let mut cmd = tokio::process::Command::new(&j_path);
    cmd.args(command)
        .env("JUMPSTARTER_HOST", &env.socket_path)
        .env("JMP_DRIVERS_ALLOW", env.drivers_allow())
        .env("_JMP_SUPPRESS_DRIVER_WARNINGS", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return json_result(json!({"error": format!("failed to spawn j: {e}")})),
    };

    let mut stdout = child.stdout.take().expect("piped stdout");
    let mut stderr = child.stderr.take().expect("piped stderr");
    let mut out = Vec::new();
    let mut err = Vec::new();
    let timed_out = {
        let drain = async {
            let _ = tokio::join!(stdout.read_to_end(&mut out), stderr.read_to_end(&mut err));
        };
        tokio::time::timeout(Duration::from_secs(timeout_seconds), drain).await.is_err()
    };
    if timed_out {
        tracing::warn!(command = ?command, timeout_seconds, "run_j timed out; killing child");
        if let Err(e) = child.start_kill() {
            tracing::debug!(error = %e, "run_j start_kill failed (child likely already exited)");
        }
    }
    let status = child.wait().await.ok();
    tracing::info!(
        command = ?command,
        exit_code = ?status.and_then(|s| s.code()),
        timed_out,
        "run_j complete"
    );

    let full_command: Vec<String> = std::iter::once(j_path.display().to_string())
        .chain(command.iter().cloned())
        .collect();
    let mut result = json!({
        "exit_code": status.and_then(|s| s.code()),
        "stdout": String::from_utf8_lossy(&out),
        "stderr": String::from_utf8_lossy(&err),
        "command": full_command,
    });
    if timed_out {
        result["timed_out"] = json!(true);
        result["timeout_seconds"] = json!(timeout_seconds);
    }
    json_result(result)
}

/// Shell out to `j introspect <sub> [extra...]` (the Python driver-client introspection side
/// channel) and return its JSON stdout. The driver clients are Python, so their Click trees +
/// method signatures can only be inspected in-process by `j`.
async fn j_introspect(
    env: &connections::ConnEnv,
    sub: &str,
    extra: &[String],
) -> Result<CallToolResult, McpError> {
    let j_path = match which::which("j") {
        Ok(p) => p,
        Err(_) => return json_result(json!({"error": "j CLI binary not found in PATH"})),
    };
    let mut cmd = tokio::process::Command::new(&j_path);
    cmd.arg("introspect")
        .arg(sub)
        .args(extra)
        .env("JUMPSTARTER_HOST", &env.socket_path)
        .env("JMP_DRIVERS_ALLOW", env.drivers_allow())
        .env("_JMP_SUPPRESS_DRIVER_WARNINGS", "1")
        .stdin(Stdio::null())
        .kill_on_drop(true);
    let output = match tokio::time::timeout(Duration::from_secs(60), cmd.output()).await {
        Ok(Ok(o)) => o,
        Ok(Err(e)) => return json_result(json!({"error": format!("failed to run j introspect: {e}")})),
        Err(_) => return json_result(json!({"error": "j introspect timed out"})),
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    if stdout.trim().is_empty() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return json_result(json!({"error": format!("j introspect produced no output: {}", stderr.trim())}));
    }
    // stdout is already JSON from `j introspect`.
    Ok(CallToolResult::success(vec![Content::text(stdout.into_owned())]))
}

/// The Jumpstarter MCP server. Holds the rmcp tool router; each tool resolves the client
/// config + connects a fresh controller session per call (mirroring the Python server).
#[derive(Clone)]
pub struct Jumpstarter {
    // Read by the `#[tool_handler]`-generated `call_tool`/`list_tools`; the lint
    // can't see through the macro + Clone derive, hence the allow.
    #[allow(dead_code)]
    tool_router: ToolRouter<Jumpstarter>,
    /// Shared (Arc-backed) persistent connections, so state survives across tool calls.
    manager: connections::ConnectionManager,
}

#[tool_router]
impl Jumpstarter {
    pub fn new() -> Self {
        Self {
            tool_router: Self::tool_router(),
            manager: connections::ConnectionManager::new(),
        }
    }

    #[tool(
        description = "List exporters registered on the Jumpstarter controller. Shows available \
                       hardware devices with their labels, online status, and current lease info."
    )]
    async fn jmp_list_exporters(
        &self,
        Parameters(args): Parameters<ListExportersArgs>,
    ) -> Result<CallToolResult, McpError> {
        let session = config::connect().await.map_err(internal)?;
        let exporters = parse_array(&session.list_exporters_json(args.selector).await.map_err(internal)?)?;
        let active = if args.include_leases {
            let leases = parse_array(&session.list_leases_json(None, true, None).await.map_err(internal)?)?;
            shape::active_leases_by_exporter(&leases)
        } else {
            Default::default()
        };
        let out = shape::shape_exporters(&exporters, &active, args.include_leases, args.include_online);
        json_result(Value::Array(out))
    }

    #[tool(description = "List active leases from the Jumpstarter controller.")]
    async fn jmp_list_leases(
        &self,
        Parameters(args): Parameters<ListLeasesArgs>,
    ) -> Result<CallToolResult, McpError> {
        let session = config::connect().await.map_err(internal)?;
        let leases = parse_array(&session.list_leases_json(args.selector, true, None).await.map_err(internal)?)?;
        json_result(Value::Array(shape::shape_leases(&leases)))
    }

    #[tool(
        description = "Create a new lease for a hardware device. Requires one of selector or \
                       exporter_name. Returns the created lease name."
    )]
    async fn jmp_create_lease(
        &self,
        Parameters(args): Parameters<CreateLeaseArgs>,
    ) -> Result<CallToolResult, McpError> {
        if args.selector.is_none() && args.exporter_name.is_none() {
            return json_result(json!({"error": "One of selector or exporter_name is required"}));
        }
        let session = config::connect().await.map_err(internal)?;
        let name = session
            .create_lease(
                args.duration_seconds,
                args.selector.clone(),
                args.exporter_name.clone(),
                std::collections::BTreeMap::new(),
            )
            .await
            .map_err(internal)?;
        json_result(json!({
            "name": name,
            "status": "created",
            "duration_seconds": args.duration_seconds,
            "selector": args.selector,
            "exporter_name": args.exporter_name,
        }))
    }

    #[tool(description = "Delete/release a lease by name.")]
    async fn jmp_delete_lease(
        &self,
        Parameters(args): Parameters<DeleteLeaseArgs>,
    ) -> Result<CallToolResult, McpError> {
        let session = config::connect().await.map_err(internal)?;
        session.release_lease(args.lease_id.clone()).await.map_err(internal)?;
        json_result(json!({"name": args.lease_id, "status": "deleted"}))
    }

    #[tool(
        description = "Connect to a hardware device, establishing a persistent background \
                       connection. Creates or acquires a lease, serves a Unix socket, and \
                       returns a connection ID for use with jmp_run / jmp_explore."
    )]
    async fn jmp_connect(
        &self,
        Parameters(args): Parameters<ConnectArgs>,
    ) -> Result<CallToolResult, McpError> {
        if args.lease_id.is_none() && args.selector.is_none() && args.exporter_name.is_none() {
            return json_result(json!({"error": "One of lease_id, selector, or exporter_name is required"}));
        }
        let (session, cfg) = config::connect_with_config().await.map_err(internal)?;
        match self
            .manager
            .connect(
                session,
                cfg.drivers.allow,
                cfg.drivers.r#unsafe,
                args.lease_id,
                args.selector,
                args.exporter_name,
                args.duration_seconds,
            )
            .await
        {
            Ok(info) => json_result(info),
            Err(e) => json_result(json!({"error": format!("Failed to connect: {e}")})),
        }
    }

    #[tool(description = "Disconnect from a device and tear down the background connection.")]
    async fn jmp_disconnect(
        &self,
        Parameters(args): Parameters<ConnectionIdArgs>,
    ) -> Result<CallToolResult, McpError> {
        match self.manager.disconnect(&args.connection_id).await {
            Ok(v) => json_result(v),
            Err(e) => json_result(json!({"error": e})),
        }
    }

    #[tool(description = "List all active persistent connections.")]
    async fn jmp_list_connections(&self) -> Result<CallToolResult, McpError> {
        json_result(self.manager.list().await)
    }

    #[tool(
        description = "Run a `j` CLI command on a connected device. Executes a subcommand \
                       against the connection, capturing stdout, stderr, and exit code. For \
                       streaming commands like \"serial pipe\", use a short timeout_seconds."
    )]
    async fn jmp_run(&self, Parameters(args): Parameters<RunArgs>) -> Result<CallToolResult, McpError> {
        let env = match self.manager.env(&args.connection_id).await {
            Ok(e) => e,
            Err(e) => return json_result(json!({"error": e})),
        };
        run_j(&env, &args.command, args.timeout_seconds).await
    }

    #[tool(
        description = "Get environment variables, paths, and code examples for direct \
                       shell/Python interaction with this connection."
    )]
    async fn jmp_get_env(
        &self,
        Parameters(args): Parameters<ConnectionIdArgs>,
    ) -> Result<CallToolResult, McpError> {
        let env = match self.manager.env(&args.connection_id).await {
            Ok(e) => e,
            Err(e) => return json_result(json!({"error": e})),
        };
        let j_path = which::which("j").ok().map(|p| p.display().to_string());
        json_result(json!({
            "connection_id": args.connection_id,
            "lease_name": env.lease_name,
            "exporter_name": env.exporter_name,
            "env": {
                "JUMPSTARTER_HOST": env.socket_path,
                "JMP_DRIVERS_ALLOW": env.drivers_allow(),
                "_JMP_SUPPRESS_DRIVER_WARNINGS": "1",
            },
            "j_path": j_path,
            "shell_example": format!("JUMPSTARTER_HOST={} j power on", env.socket_path),
            "python_example": "from jumpstarter.utils.env import env\n\nwith env() as client:\n    client.power.on()\n    client.power.off()\n",
            "note": "Run shell commands or Python scripts with these env vars set. The env() helper reads JUMPSTARTER_HOST automatically.",
        }))
    }

    #[tool(
        description = "Explore available CLI commands for a connected device. Walks the Click \
                       command tree (names, help, params, nested subcommands). Pass command_path \
                       to drill into a subtree (e.g. [\"storage\"])."
    )]
    async fn jmp_explore(&self, Parameters(args): Parameters<ExploreArgs>) -> Result<CallToolResult, McpError> {
        let env = match self.manager.env(&args.connection_id).await {
            Ok(e) => e,
            Err(e) => return json_result(json!({"error": e})),
        };
        j_introspect(&env, "explore", &args.command_path.unwrap_or_default()).await
    }

    #[tool(
        description = "List all driver objects in the connected device's driver tree (path, \
                       Python class, description, method names)."
    )]
    async fn jmp_drivers(
        &self,
        Parameters(args): Parameters<ConnectionIdArgs>,
    ) -> Result<CallToolResult, McpError> {
        let env = match self.manager.env(&args.connection_id).await {
            Ok(e) => e,
            Err(e) => return json_result(json!({"error": e})),
        };
        j_introspect(&env, "drivers", &[]).await
    }

    #[tool(
        description = "Inspect methods on a specific driver client: signatures, docstrings, \
                       parameters, and ready-to-use call examples."
    )]
    async fn jmp_driver_methods(
        &self,
        Parameters(args): Parameters<DriverMethodsArgs>,
    ) -> Result<CallToolResult, McpError> {
        let env = match self.manager.env(&args.connection_id).await {
            Ok(e) => e,
            Err(e) => return json_result(json!({"error": e})),
        };
        j_introspect(&env, "driver-methods", &args.driver_path).await
    }
}

impl Default for Jumpstarter {
    fn default() -> Self {
        Self::new()
    }
}

#[tool_handler]
impl ServerHandler for Jumpstarter {
    fn get_info(&self) -> ServerInfo {
        let mut implementation = Implementation::from_build_env();
        implementation.name = "jumpstarter".to_string();
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(implementation)
            .with_protocol_version(ProtocolVersion::V_2024_11_05)
            .with_instructions(SERVER_INSTRUCTIONS.to_string())
    }
}

/// Run the MCP server over stdio until the client disconnects.
pub async fn run_server() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let service = Jumpstarter::new().serve(stdio()).await?;
    service.waiting().await?;
    Ok(())
}
