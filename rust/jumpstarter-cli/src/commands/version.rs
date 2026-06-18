//! `jmp version` (spec 08 §12). Default human string, or `-o json|yaml` with the
//! contract field `gitVersion`. Python embeds the interpreter version; a Rust binary
//! reports its own build instead.

use std::process::ExitCode;

use clap::Args as ClapArgs;
use serde::Serialize;

use crate::output;

#[derive(Clone, Copy, clap::ValueEnum)]
#[value(rename_all = "lower")]
enum VersionFormat {
    Json,
    Yaml,
}

#[derive(ClapArgs)]
pub struct Args {
    /// Output format (default: human-readable).
    #[arg(long, short = 'o', value_enum)]
    output: Option<VersionFormat>,
}

#[derive(Serialize)]
struct VersionInfo {
    #[serde(rename = "gitVersion")]
    git_version: String,
}

pub fn run(args: Args) -> ExitCode {
    let version = format!("v{}", env!("CARGO_PKG_VERSION"));
    match args.output {
        Some(VersionFormat::Json) => {
            let info = VersionInfo {
                git_version: version,
            };
            println!("{}", output::to_json(&info).unwrap_or_default());
        }
        Some(VersionFormat::Yaml) => {
            let info = VersionInfo {
                git_version: version,
            };
            print!("{}", output::to_yaml(&info).unwrap_or_default());
        }
        None => {
            let path = std::env::current_exe()
                .map(|p| p.display().to_string())
                .unwrap_or_else(|_| "?".to_string());
            println!("Jumpstarter {version} from {path}");
        }
    }
    ExitCode::SUCCESS
}
