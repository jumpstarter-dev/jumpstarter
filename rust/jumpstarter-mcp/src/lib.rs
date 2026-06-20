//! Rust MCP server exposing Jumpstarter hardware-management tools over stdio.
//!
//! Replaces the Python `jumpstarter-mcp` package. The controller/lease tools run on the
//! Rust core (`jumpstarter_core::ControllerSession`); the connection/run/introspection
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

mod config;
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

/// The Jumpstarter MCP server. Holds the rmcp tool router; each tool resolves the client
/// config + connects a fresh controller session per call (mirroring the Python server).
#[derive(Clone)]
pub struct Jumpstarter {
    // Read by the `#[tool_handler]`-generated `call_tool`/`list_tools`; the lint
    // can't see through the macro + Clone derive, hence the allow.
    #[allow(dead_code)]
    tool_router: ToolRouter<Jumpstarter>,
}

#[tool_router]
impl Jumpstarter {
    pub fn new() -> Self {
        Self {
            tool_router: Self::tool_router(),
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
