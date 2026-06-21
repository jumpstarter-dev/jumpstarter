//! `jmp auth {status,refresh,rotate}` (spec 08 §8.1-8.3; `auth.py`).


use chrono::{TimeZone, Utc};
use clap::{Args as ClapArgs, Subcommand};
use jumpstarter_client::ControllerClient;
use jumpstarter_config::YamlConfig;
use owo_colors::{OwoColorize, Stream::Stdout};

use jumpstarter_auth::jwt;
use jumpstarter_auth::oidc::OidcConfig;

use crate::clientcfg::ConfigOpts;
use crate::cmderr::{grpc, runtime, CmdError};

const TOKEN_EXPIRY_WARNING_SECONDS: i64 = 300;

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Display token status and expiry information.
    Status(StatusArgs),
    /// Refresh the access token using a stored refresh token.
    Refresh(RefreshArgs),
    /// Rotate the internal token, replacing it with a freshly signed one.
    Rotate(RefreshArgs),
}

pub async fn run(args: Args) -> u8 {
    let result = match args.command {
        Command::Status(a) => status(a),
        Command::Refresh(a) => refresh(a).await,
        Command::Rotate(a) => rotate(a).await,
    };
    match result {
        Ok(()) => 0,
        Err(e) => e.report(),
    }
}

// ---- styling / formatting helpers -----------------------------------------

fn yellow(s: &str) -> String {
    s.if_supports_color(Stdout, |t| t.yellow()).to_string()
}
fn red(s: &str) -> String {
    s.if_supports_color(Stdout, |t| t.red()).to_string()
}
fn red_bold(s: &str) -> String {
    let style = owo_colors::Style::new().red().bold();
    s.if_supports_color(Stdout, |t| t.style(style)).to_string()
}
fn green(s: &str) -> String {
    s.if_supports_color(Stdout, |t| t.green()).to_string()
}

/// `datetime.fromtimestamp(v, tz=utc).strftime('%Y-%m-%d %H:%M:%S %Z')`.
fn fmt_ts(secs: i64) -> String {
    Utc.timestamp_opt(secs, 0)
        .single()
        .map(|dt| dt.format("%Y-%m-%d %H:%M:%S %Z").to_string())
        .unwrap_or_default()
}

/// `format_duration` (`oidc.py:219`): `{h}h {m}m`, else `{m}m {s}s`, else `{s}s`
/// (magnitude only).
fn format_duration(seconds: i64) -> String {
    let abs = seconds.unsigned_abs();
    let hours = abs / 3600;
    let mins = (abs % 3600) / 60;
    let secs = abs % 60;
    if hours > 0 {
        format!("{hours}h {mins}m")
    } else if mins > 0 {
        format!("{mins}m {secs}s")
    } else {
        format!("{secs}s")
    }
}

// ---- status ---------------------------------------------------------------

#[derive(ClapArgs)]
struct StatusArgs {
    #[command(flatten)]
    config: ConfigOpts,
    /// Show additional token details.
    #[arg(short = 'v', long)]
    verbose: bool,
}

fn status(a: StatusArgs) -> Result<(), CmdError> {
    let config = a.config.resolve()?;
    let token = match config.token.as_deref().filter(|t| !t.is_empty()) {
        Some(t) => t,
        None => {
            println!("{}", yellow("No token found in config"));
            return Ok(());
        }
    };

    let claims = match jwt::decode_claims(token) {
        Ok(c) => c,
        Err(e) => {
            println!("{}", red(&format!("Failed to decode token: {e}")));
            return Ok(());
        }
    };

    let Some(remaining) = jwt::remaining_seconds(token) else {
        println!("{}", yellow("Token has no expiry claim"));
        return Ok(());
    };

    let exp = claims.get_i64("exp").unwrap_or_default();
    println!("Token expiry: {}", fmt_ts(exp));
    print_token_status(remaining);

    if let Some(sub) = claims.get_str("sub") {
        println!("Subject: {sub}");
    }
    if let Some(iss) = claims.get_str("iss") {
        println!("Issuer: {iss}");
    }

    if a.verbose {
        if let Some(iat) = claims.get_i64("iat") {
            println!("Issued at: {}", fmt_ts(iat));
        }
        if let Some(auth_time) = claims.get_i64("auth_time") {
            println!("Auth time: {}", fmt_ts(auth_time));
        }
        let stored = config
            .refresh_token
            .as_deref()
            .is_some_and(|s| !s.is_empty());
        println!(
            "Refresh token stored: {}",
            if stored { "yes" } else { "no" }
        );
    }
    Ok(())
}

fn print_token_status(remaining: i64) {
    let duration = format_duration(remaining);
    let hint = "Run 'jmp login' to refresh your credentials.";
    if remaining < 0 {
        println!("{}", red_bold(&format!("Status: EXPIRED ({duration} ago)")));
        println!("{}", yellow(hint));
    } else if remaining < TOKEN_EXPIRY_WARNING_SECONDS {
        println!(
            "{}",
            red_bold(&format!("Status: EXPIRING SOON ({duration} remaining)"))
        );
        println!("{}", yellow(hint));
    } else if remaining < 3600 {
        println!(
            "{}",
            yellow(&format!("Status: Valid ({duration} remaining)"))
        );
    } else {
        println!(
            "{}",
            green(&format!("Status: Valid ({duration} remaining)"))
        );
    }
}

// ---- refresh / rotate -----------------------------------------------------

#[derive(ClapArgs)]
struct RefreshArgs {
    #[command(flatten)]
    config: ConfigOpts,
}

async fn refresh(a: RefreshArgs) -> Result<(), CmdError> {
    let (mut config, path) = a.config.resolve_with_path()?;

    let refresh_token = config
        .refresh_token
        .clone()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            CmdError::Runtime(
                "No refresh token found. Run 'jmp login --offline-access'.".to_string(),
            )
        })?;
    let access_token = config
        .token
        .clone()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            CmdError::Runtime(
                "No access token found. Run 'jmp login --offline-access'.".to_string(),
            )
        })?;

    let issuer = jwt::issuer(&access_token)
        .map_err(|e| CmdError::Runtime(format!("Failed to decode JWT issuer: {e}")))?
        .ok_or_else(|| {
            CmdError::Runtime("Failed to determine issuer from access token.".to_string())
        })?;

    let oidc = OidcConfig::new(issuer, "jumpstarter-cli");
    let tokens = oidc
        .refresh_token_grant(&refresh_token)
        .await
        .map_err(runtime)?;

    config.token = Some(tokens.access_token);
    if let Some(rt) = tokens.refresh_token {
        config.refresh_token = Some(rt);
    }
    config.save(&path).map_err(runtime)?;
    println!("Access token refreshed.");
    Ok(())
}

async fn rotate(a: RefreshArgs) -> Result<(), CmdError> {
    let (mut config, path) = a.config.resolve_with_path()?;

    let token = config
        .token
        .clone()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| CmdError::Runtime("No token found in config.".to_string()))?;

    if jwt::remaining_seconds(&token).is_some_and(|r| r < 0) {
        return Err(CmdError::Runtime(
            "Token is expired. Cannot rotate — recreate the client with 'jmp config client create'."
                .to_string(),
        ));
    }

    let controller = ControllerClient::connect(&config).await.map_err(grpc)?;
    let new_token = controller.rotate_token().await.map_err(grpc)?.token;
    if new_token.is_empty() {
        return Err(CmdError::Runtime(
            "Token rotation failed: empty token received.".to_string(),
        ));
    }

    let claims = jwt::decode_claims(&new_token).map_err(|e| {
        CmdError::Runtime(format!(
            "Token rotation failed: invalid token returned ({e})."
        ))
    })?;

    config.token = Some(new_token.clone());
    config.save(&path).map_err(runtime)?;

    match jwt::remaining_seconds(&new_token) {
        Some(remaining) => {
            let duration = format_duration(remaining);
            match claims.get_i64("exp").filter(|&e| e != 0) {
                Some(exp) => println!(
                    "Token rotated. New expiry: {} ({duration} remaining)",
                    fmt_ts(exp)
                ),
                None => println!("Token rotated. {duration} remaining."),
            }
        }
        None => println!("Token rotated."),
    }
    Ok(())
}
