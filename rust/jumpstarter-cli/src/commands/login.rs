//! `jmp login` (spec 08 §8.4; `login.py`). Fetches login/auth config, resolves or
//! creates a client/exporter config, runs an OIDC grant, and persists the tokens.

use std::path::PathBuf;
use std::time::Duration;

use clap::Args as ClapArgs;
use jumpstarter_config::{
    client_from_env, paths, ClientConfig, DriversConfig, ExporterConfig, ObjectMeta, TlsConfig,
    YamlConfig,
};

use crate::cmderr::{runtime, CmdError};
use crate::oidc::OidcConfig;
use crate::{jwt, prompt, userconfig};

#[derive(ClapArgs)]
pub struct Args {
    /// `[client-name@]login.endpoint`.
    login_target: Option<String>,
    /// The Jumpstarter service (gRPC) endpoint.
    #[arg(short = 'e', long)]
    endpoint: Option<String>,
    /// The Jumpstarter namespace.
    #[arg(long)]
    namespace: Option<String>,
    /// The Jumpstarter client/exporter name.
    #[arg(long)]
    name: Option<String>,
    /// OIDC issuer.
    #[arg(long)]
    issuer: Option<String>,
    /// OIDC client id.
    #[arg(long = "client-id", default_value = "jumpstarter-cli")]
    client_id: String,
    /// OIDC access token (token-exchange grant).
    #[arg(long)]
    token: Option<String>,
    /// OIDC username (password grant).
    #[arg(long)]
    username: Option<String>,
    /// OIDC password (password grant).
    #[arg(long)]
    password: Option<String>,
    /// OIDC token-exchange connector id (Dex-specific).
    #[arg(long = "connector-id")]
    connector_id: Option<String>,
    /// Port for the OIDC callback server (0 = random).
    #[arg(long = "callback-port", value_parser = clap::value_parser!(u16))]
    callback_port: Option<u16>,
    /// Request the `offline_access` scope (refresh token). On by default.
    #[arg(long = "offline-access")]
    _offline_access: bool,
    #[arg(long = "no-offline-access")]
    no_offline_access: bool,
    /// A comma-separated list of driver client packages to load.
    #[arg(long, default_value = "")]
    allow: String,
    /// Allow all driver client packages to load (UNSAFE!).
    #[arg(long = "unsafe", num_args = 0..=1, default_missing_value = "true")]
    unsafe_drivers: Option<bool>,
    /// Disable TLS certificate verification.
    #[arg(short = 'k', long = "insecure-tls")]
    insecure_tls: bool,
    /// Fail instead of prompting for missing values.
    #[arg(long)]
    nointeractive: bool,
    #[command(flatten)]
    config: LoginConfigOpts,
}

#[derive(ClapArgs)]
struct LoginConfigOpts {
    #[arg(long)]
    client: Option<String>,
    #[arg(long = "client-config")]
    client_config: Option<PathBuf>,
    #[arg(long)]
    exporter: Option<String>,
    #[arg(long = "exporter-config")]
    exporter_config: Option<PathBuf>,
}

pub async fn run(args: Args) -> u8 {
    match login(args).await {
        Ok(()) => 0,
        Err(e) => e.report(),
    }
}

/// What `opt_config(allow_missing=True)` resolves to.
enum Target {
    ExistingClient {
        config: Box<ClientConfig>,
        alias: String,
        path: PathBuf,
    },
    ExistingExporter {
        config: Box<ExporterConfig>,
        path: PathBuf,
    },
    /// A config to create: (kind, alias-or-default, save path). `is_client`
    /// distinguishes client vs exporter; `sets_default` mirrors the
    /// `client`/`client_config` default-setting rule.
    New {
        is_client: bool,
        alias: String,
        path: PathBuf,
        sets_default: bool,
    },
}

async fn login(args: Args) -> Result<(), CmdError> {
    confirm_insecure_tls(args.insecure_tls, args.nointeractive)?;

    let mut endpoint = args.endpoint.clone();
    let mut namespace = args.namespace.clone();
    let mut name = args.name.clone();
    let mut issuer = args.issuer.clone();
    let mut client_id = args.client_id.clone();
    let mut allow = args.allow.clone();
    let mut unsafe_drivers = args.unsafe_drivers;
    let offline_access = !args.no_offline_access;

    // ---- simplified target: [client-name@]login.endpoint --------------------
    let mut ca_bundle: Option<String> = None;
    let mut parsed_client_name: Option<String> = None;
    if let Some(target) = &args.login_target {
        let (client_name, login_endpoint) = parse_login_argument(target)?;
        parsed_client_name = client_name.clone();
        if let (Some(cn), None) = (&client_name, &name) {
            name = Some(cn.clone());
        }
        println!("Fetching configuration from {login_endpoint}...");
        let auth = fetch_auth_config(&login_endpoint, args.insecure_tls).await?;
        if endpoint.is_none() {
            endpoint = auth
                .get("grpcEndpoint")
                .and_then(|v| v.as_str())
                .map(String::from);
        }
        if namespace.is_none() {
            namespace = auth
                .get("namespace")
                .and_then(|v| v.as_str())
                .map(String::from);
        }
        if issuer.is_none() {
            if let Some(first) = auth
                .get("oidc")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
            {
                issuer = first
                    .get("issuer")
                    .and_then(|v| v.as_str())
                    .map(String::from);
                if client_id == "jumpstarter-cli" {
                    if let Some(cid) = first.get("clientId").and_then(|v| v.as_str()) {
                        client_id = cid.to_string();
                    }
                }
            }
        }
        ca_bundle = auth
            .get("caBundle")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(String::from);
        if ca_bundle.is_some() {
            println!("Retrieved CA certificate from login service.");
        }
    }

    // ---- resolve which config we are logging into ---------------------------
    let mut target = resolve_target(&args.config)?;
    // A parsed client name overrides an unrelated existing default.
    if let (Some(pcn), Target::ExistingClient { alias, .. }) = (&parsed_client_name, &target) {
        if alias != pcn {
            target = Target::New {
                is_client: true,
                alias: pcn.clone(),
                path: paths::client_config_path(pcn),
                sets_default: true,
            };
        }
    }

    // The mutable config + bookkeeping we carry through the grant.
    let mut client_config: Option<ClientConfig> = None;
    let mut exporter_config: Option<ExporterConfig> = None;
    let save_path: PathBuf;
    let mut client_alias: Option<String> = None;
    let sets_default;

    match target {
        Target::ExistingClient {
            config,
            alias,
            path,
        } => {
            let token = config
                .token
                .clone()
                .filter(|t| !t.is_empty())
                .ok_or_else(|| {
                    CmdError::Runtime(
                        "No token set in client config. Please login again.".to_string(),
                    )
                })?;
            issuer = jwt::issuer(&token).map_err(runtime)?;
            save_path = path;
            client_alias = Some(alias);
            sets_default = false;
            client_config = Some(*config);
        }
        Target::ExistingExporter { config, path } => {
            let token = config
                .token
                .clone()
                .filter(|t| !t.is_empty())
                .ok_or_else(|| {
                    CmdError::Runtime(
                        "No token set in exporter config. Please login again.".to_string(),
                    )
                })?;
            issuer = jwt::issuer(&token).map_err(runtime)?;
            save_path = path;
            sets_default = false;
            exporter_config = Some(*config);
        }
        Target::New {
            is_client,
            alias,
            path,
            sets_default: sd,
        } => {
            save_path = path;
            sets_default = sd;
            let namespace = require(
                namespace,
                args.nointeractive,
                "Namespace",
                "Enter the Jumpstarter exporter namespace",
            )?;
            let name = require(
                name,
                args.nointeractive,
                "Name",
                "Enter the Jumpstarter exporter name",
            )?;
            let endpoint_v = require(
                endpoint.clone(),
                args.nointeractive,
                "Endpoint",
                "Enter the Jumpstarter service endpoint",
            )?;

            if is_client {
                if unsafe_drivers.is_none() {
                    unsafe_drivers = Some(if args.nointeractive {
                        false
                    } else {
                        prompt::confirm("Allow unsafe driver client imports?", false)?
                    });
                }
                if unsafe_drivers == Some(false) && allow.is_empty() {
                    if args.nointeractive {
                        return Err(CmdError::Usage(
                            "--allow TEXT or --unsafe is required in non-interactive mode."
                                .to_string(),
                        ));
                    }
                    allow = prompt::default(
                        "Enter a comma-separated list of allowed driver packages (optional)",
                        "",
                    )?;
                }
            }

            let tls = TlsConfig {
                insecure: args.insecure_tls,
                ca: ca_bundle.clone().unwrap_or_default(),
            };
            if is_client {
                let mut c = ClientConfig::new(ObjectMeta {
                    namespace: Some(namespace),
                    name,
                });
                c.tls = tls;
                c.endpoint = Some(endpoint_v);
                c.token = Some(String::new());
                let allow_list: Vec<String> = allow.split(',').map(String::from).collect();
                c.drivers = DriversConfig {
                    r#unsafe: unsafe_drivers.unwrap_or(false)
                        || allow_list.iter().any(|d| d == "UNSAFE"),
                    allow: allow_list,
                };
                client_alias = Some(alias);
                client_config = Some(c);
            } else {
                let mut c = ExporterConfig::new(ObjectMeta {
                    namespace: Some(namespace),
                    name,
                });
                c.tls = tls;
                c.endpoint = Some(endpoint_v);
                c.token = Some(String::new());
                exporter_config = Some(c);
            }
        }
    }

    warn_exporter_only_flags(exporter_config.is_some(), &allow, unsafe_drivers);

    // ---- issuer + OIDC client ----------------------------------------------
    let issuer = match issuer {
        Some(i) => i,
        None => {
            if args.nointeractive {
                return Err(CmdError::Usage(
                    "Issuer is required in non-interactive mode.".to_string(),
                ));
            }
            prompt::value(None, "Enter the OIDC issuer")?
        }
    };

    let stored_refresh = client_config.as_ref().and_then(|c| c.refresh_token.clone());
    let oidc = OidcConfig {
        issuer,
        client_id,
        offline_access: offline_access || stored_refresh.is_some(),
        insecure_tls: args.insecure_tls,
    };

    // ---- grant selection ----------------------------------------------------
    // Stored refresh token short-circuit.
    if let Some(rt) = stored_refresh.as_deref() {
        if args.token.is_none() && args.username.is_none() && args.password.is_none() {
            match oidc.refresh_token_grant(rt).await {
                Ok(tokens) => {
                    let c = client_config.as_mut().unwrap();
                    c.token = Some(tokens.access_token);
                    if let Some(new_rt) = tokens.refresh_token {
                        c.refresh_token = Some(new_rt);
                    }
                    c.save(&save_path).map_err(runtime)?;
                    println!("Refreshed access token using stored refresh token.");
                    return Ok(());
                }
                Err(e) => {
                    if args.nointeractive {
                        return Err(CmdError::Runtime(format!(
                            "Failed to refresh access token: {e}"
                        )));
                    }
                    // fall through to an interactive grant
                }
            }
        }
    }

    let tokens = if let Some(token) = &args.token {
        oidc.token_exchange_grant(token, args.connector_id.as_deref())
            .await
            .map_err(runtime)?
    } else if let (Some(u), Some(p)) = (&args.username, &args.password) {
        oidc.password_grant(u, p).await.map_err(runtime)?
    } else {
        oidc.authorization_code_grant(args.callback_port)
            .await
            .map_err(runtime)?
    };

    // ---- persist ------------------------------------------------------------
    if let Some(c) = client_config.as_mut() {
        c.token = Some(tokens.access_token);
        if let Some(rt) = tokens.refresh_token {
            c.refresh_token = Some(rt);
        }
        c.save(&save_path).map_err(runtime)?;
    } else if let Some(c) = exporter_config.as_mut() {
        c.token = Some(tokens.access_token);
        // exporter configs do not store refresh tokens
        c.save(&save_path).map_err(runtime)?;
    }

    if sets_default {
        if let Some(alias) = &client_alias {
            let mut user = userconfig::load_or_create()?;
            userconfig::use_client(&mut user, Some(alias))?;
            println!("Set '{alias}' as the default client.");
        }
    }
    Ok(())
}

// ---- helpers --------------------------------------------------------------

fn confirm_insecure_tls(insecure: bool, nointeractive: bool) -> Result<(), CmdError> {
    if insecure && !nointeractive {
        let ok = prompt::confirm(
            "Insecure TLS mode is enabled. Certificate verification will be disabled for HTTPS connections. Continue?",
            false,
        )?;
        if !ok {
            // click echoes "Aborting." then raises Abort, which `handle_exceptions`
            // renders as "Error: Aborted by user." (exit 1).
            println!("Aborting.");
            return Err(CmdError::Runtime("Aborted by user.".to_string()));
        }
    }
    Ok(())
}

/// Parse `[client-name@]endpoint` (`login.py:parse_login_argument`).
fn parse_login_argument(arg: &str) -> Result<(Option<String>, String), CmdError> {
    let arg = arg.trim();
    if arg.is_empty() {
        return Err(CmdError::Runtime(
            "Login target cannot be empty.".to_string(),
        ));
    }
    match arg.rsplit_once('@') {
        Some((client, endpoint)) => {
            let client = client.trim();
            let endpoint = endpoint.trim();
            if client.is_empty() {
                return Err(CmdError::Runtime(
                    "Client name before '@' cannot be empty.".to_string(),
                ));
            }
            if endpoint.is_empty() {
                return Err(CmdError::Runtime(
                    "Login endpoint after '@' cannot be empty.".to_string(),
                ));
            }
            Ok((Some(client.to_string()), endpoint.to_string()))
        }
        None => Ok((None, arg.to_string())),
    }
}

async fn fetch_auth_config(endpoint: &str, insecure: bool) -> Result<serde_json::Value, CmdError> {
    if endpoint.starts_with("http://") && !insecure {
        return Err(CmdError::Usage(
            "HTTP login endpoints require --insecure-tls / -k.".to_string(),
        ));
    }
    let base = if endpoint.starts_with("http://") || endpoint.starts_with("https://") {
        endpoint.to_string()
    } else {
        format!("https://{endpoint}")
    };
    let url = format!("{}/v1/auth/config", base.trim_end_matches('/'));
    // Honor SSL_CERT_FILE (custom CA) like Python, with a 30s request budget.
    let client = crate::oidc::build_http_client(insecure).map_err(runtime)?;
    let resp = client
        .get(&url)
        .timeout(Duration::from_secs(30))
        .send()
        .await
        .map_err(|e| {
            CmdError::Runtime(format!("Failed to fetch auth config from {endpoint}: {e}"))
        })?;
    if resp.status().as_u16() != 200 {
        return Err(CmdError::Runtime(format!(
            "Failed to fetch auth config from {url}: HTTP {}",
            resp.status().as_u16()
        )));
    }
    let payload: serde_json::Value = resp.json().await.map_err(|_| {
        CmdError::Runtime(format!(
            "Invalid JSON response received from {url}. Verify the login endpoint or proxy configuration."
        ))
    })?;
    if !payload.is_object() {
        return Err(CmdError::Runtime(format!(
            "Invalid auth config response from {url}: expected a JSON object."
        )));
    }
    let grpc_ok = payload
        .get("grpcEndpoint")
        .and_then(|v| v.as_str())
        .is_some_and(|s| !s.trim().is_empty());
    if !grpc_ok {
        return Err(CmdError::Runtime(format!(
            "Invalid auth config response from {url}: missing required field 'grpcEndpoint'."
        )));
    }
    Ok(payload)
}

/// Resolve `opt_config(allow_missing=True)` to a [`Target`].
fn resolve_target(opts: &LoginConfigOpts) -> Result<Target, CmdError> {
    let count = [
        opts.client.is_some(),
        opts.client_config.is_some(),
        opts.exporter.is_some(),
        opts.exporter_config.is_some(),
    ]
    .iter()
    .filter(|x| **x)
    .count();
    if count > 1 {
        return Err(CmdError::Usage(
            "only one of --client, --client-config, --exporter, --exporter-config should be specified"
                .to_string(),
        ));
    }

    if let Some(alias) = &opts.client {
        let path = paths::client_config_path(alias);
        return Ok(match ClientConfig::load(&path) {
            Ok(config) => Target::ExistingClient {
                config: Box::new(config),
                alias: alias.clone(),
                path,
            },
            Err(_) => Target::New {
                is_client: true,
                alias: alias.clone(),
                path,
                sets_default: true,
            },
        });
    }
    if let Some(path) = &opts.client_config {
        return Ok(match ClientConfig::load(path) {
            Ok(config) => Target::ExistingClient {
                config: Box::new(config),
                alias: paths::alias_from_path(path).unwrap_or_else(|| "default".to_string()),
                path: path.clone(),
            },
            Err(_) => Target::New {
                is_client: true,
                alias: "default".to_string(),
                path: path.clone(),
                sets_default: true,
            },
        });
    }
    if let Some(alias) = &opts.exporter {
        let path = paths::resolve_exporter_path(alias);
        return Ok(match ExporterConfig::load(&path) {
            Ok(config) => Target::ExistingExporter {
                config: Box::new(config),
                path: paths::exporter_user_path(alias),
            },
            Err(_) => Target::New {
                is_client: false,
                alias: alias.clone(),
                path: paths::exporter_user_path(alias),
                sets_default: false,
            },
        });
    }
    if let Some(path) = &opts.exporter_config {
        return Ok(match ExporterConfig::load(path) {
            Ok(config) => Target::ExistingExporter {
                config: Box::new(config),
                path: path.clone(),
            },
            Err(_) => Target::New {
                is_client: false,
                alias: "default".to_string(),
                path: path.clone(),
                sets_default: false,
            },
        });
    }

    // No flag: env-built client, else the user's current client, else a new
    // "default" client.
    if let Some(config) = client_from_env() {
        return Ok(Target::ExistingClient {
            config: Box::new(config),
            alias: "default".to_string(),
            path: paths::client_config_path("default"),
        });
    }
    let user = userconfig::load_or_create()?;
    if let Some(alias) = user.current_client() {
        let path = paths::client_config_path(alias);
        if let Ok(config) = ClientConfig::load(&path) {
            return Ok(Target::ExistingClient {
                config: Box::new(config),
                alias: alias.to_string(),
                path,
            });
        }
    }
    Ok(Target::New {
        is_client: true,
        alias: "default".to_string(),
        path: paths::client_config_path("default"),
        sets_default: true,
    })
}

/// A value that is required: returned if present, prompted otherwise, or an error
/// in non-interactive mode.
fn require(
    value: Option<String>,
    nointeractive: bool,
    field: &str,
    prompt_text: &str,
) -> Result<String, CmdError> {
    match value {
        Some(v) => Ok(v),
        None if nointeractive => Err(CmdError::Usage(format!(
            "{field} is required in non-interactive mode."
        ))),
        None => prompt::value(None, prompt_text).map_err(CmdError::Runtime),
    }
}

fn warn_exporter_only_flags(is_exporter: bool, allow: &str, unsafe_drivers: Option<bool>) {
    if is_exporter {
        if !allow.is_empty() {
            println!("Warning: --allow is ignored for exporter configs (only applies to client configs).");
        }
        if unsafe_drivers == Some(true) {
            println!("Warning: --unsafe is ignored for exporter configs (only applies to client configs).");
        }
    }
}

// Ported from the deleted Python `jumpstarter_cli/login_test.py`: `[client@]endpoint`
// parsing and the http-without-opt-in rejection.
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_supports_client_and_endpoint() {
        let (client, endpoint) = parse_login_argument("my-client@login.example.com").unwrap();
        assert_eq!(client.as_deref(), Some("my-client"));
        assert_eq!(endpoint, "login.example.com");
    }

    #[test]
    fn parse_without_client_is_endpoint_only() {
        let (client, endpoint) = parse_login_argument("login.example.com").unwrap();
        assert_eq!(client, None);
        assert_eq!(endpoint, "login.example.com");
    }

    #[test]
    fn parse_trims_client_and_endpoint() {
        let (client, endpoint) = parse_login_argument("  my-client  @  login.example.com  ").unwrap();
        assert_eq!(client.as_deref(), Some("my-client"));
        assert_eq!(endpoint, "login.example.com");
    }

    #[test]
    fn parse_rejects_empty_target() {
        assert!(parse_login_argument("   ").is_err());
    }

    #[test]
    fn parse_rejects_empty_client_name() {
        assert!(parse_login_argument("@login.example.com").is_err());
    }

    #[test]
    fn parse_rejects_whitespace_only_endpoint() {
        assert!(parse_login_argument("my-client@   ").is_err());
    }

    #[tokio::test]
    async fn fetch_auth_config_rejects_http_without_opt_in() {
        // The scheme check returns before any network access.
        let err = fetch_auth_config("http://login.example.com", false).await.unwrap_err();
        assert!(format!("{err:?}").contains("HTTP"), "{err:?}");
    }
}
