//! `jmp completion {bash,zsh,fish}` (spec 08 §12). The runtime protocol is
//! clap-native (not click's `_JMP_COMPLETE`), but the `jmp completion SHELL`
//! invocation and the accepted shells are preserved; other shells are a usage error
//! (exit 2) via the value enum.

use clap::{Args as ClapArgs, CommandFactory};
use clap_complete::{generate, Shell};

#[derive(Clone, Copy, clap::ValueEnum)]
#[value(rename_all = "lower")]
enum CompletionShell {
    Bash,
    Zsh,
    Fish,
}

#[derive(ClapArgs)]
pub struct Args {
    /// Shell to generate a completion script for.
    #[arg(value_enum)]
    shell: CompletionShell,
}

pub fn run<C: CommandFactory>(args: Args) -> u8 {
    let shell = match args.shell {
        CompletionShell::Bash => Shell::Bash,
        CompletionShell::Zsh => Shell::Zsh,
        CompletionShell::Fish => Shell::Fish,
    };
    let mut cmd = C::command();
    let name = cmd.get_name().to_string();
    generate(shell, &mut cmd, name, &mut std::io::stdout());
    0
}
