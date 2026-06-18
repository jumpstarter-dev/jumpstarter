//! Interactive prompt helpers (the Rust equivalent of click's `prompt=`), used
//! when a required option is omitted. On a TTY these use rich dialoguer widgets;
//! when stdin is piped (non-interactive, e.g. tests/CI) they fall back to reading
//! a line, matching click's behavior of consuming piped answers instead of
//! failing. The prompt text is echoed to stderr so stdout stays parseable.

use std::io::{self, BufRead, IsTerminal, Write};

use dialoguer::{Confirm, Input, Password};

fn read_line() -> Result<String, String> {
    let mut s = String::new();
    io::stdin()
        .lock()
        .read_line(&mut s)
        .map_err(|e| e.to_string())?;
    Ok(s.trim_end_matches(['\n', '\r']).to_string())
}

fn echo(prompt: &str) {
    eprint!("{prompt}: ");
    let _ = io::stderr().flush();
}

/// Prompt for a required string when `value` is absent.
pub fn value(value: Option<String>, prompt: &str) -> Result<String, String> {
    if let Some(v) = value {
        return Ok(v);
    }
    if io::stdin().is_terminal() {
        Input::<String>::new()
            .with_prompt(prompt)
            .interact_text()
            .map_err(|e| e.to_string())
    } else {
        echo(prompt);
        read_line()
    }
}

/// Prompt for an optional string with a default, allowing an empty answer.
pub fn default(prompt: &str, default: &str) -> Result<String, String> {
    if io::stdin().is_terminal() {
        Input::<String>::new()
            .with_prompt(prompt)
            .allow_empty(true)
            .default(default.to_string())
            .interact_text()
            .map_err(|e| e.to_string())
    } else {
        eprint!("{prompt} [{default}]: ");
        let _ = io::stderr().flush();
        let line = read_line()?;
        Ok(if line.is_empty() {
            default.to_string()
        } else {
            line
        })
    }
}

/// Prompt (hidden input) for a required secret when `value` is absent.
pub fn password(value: Option<String>, prompt: &str) -> Result<String, String> {
    if let Some(v) = value {
        return Ok(v);
    }
    if io::stdin().is_terminal() {
        Password::new()
            .with_prompt(prompt)
            .interact()
            .map_err(|e| e.to_string())
    } else {
        echo(prompt);
        read_line()
    }
}

/// Yes/no confirmation prompt (used by `login` for the insecure-TLS warning).
#[allow(dead_code)]
pub fn confirm(prompt: &str, default_yes: bool) -> Result<bool, String> {
    if io::stdin().is_terminal() {
        Confirm::new()
            .with_prompt(prompt)
            .default(default_yes)
            .interact()
            .map_err(|e| e.to_string())
    } else {
        eprint!("{prompt} [{}]: ", if default_yes { "Y/n" } else { "y/N" });
        let _ = io::stderr().flush();
        let line = read_line()?.to_ascii_lowercase();
        Ok(match line.as_str() {
            "" => default_yes,
            "y" | "yes" => true,
            _ => false,
        })
    }
}
