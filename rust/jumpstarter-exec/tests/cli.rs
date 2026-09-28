use std::process::Command;

#[test]
fn version_subcommand() {
    let output = Command::new(env!("CARGO_BIN_EXE_jumpstarter-exec"))
        .arg("version")
        .output()
        .expect("failed to run jumpstarter-exec version");
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert_eq!(
        stdout.trim(),
        concat!("jumpstarter-exec ", env!("GIT_VERSION"))
    );
    assert!(output.stderr.is_empty());
}

#[cfg(not(unix))]
#[test]
fn execution_commands_report_unsupported_platform() {
    for command in ["serve", "exec", "shutdown"] {
        let output = Command::new(env!("CARGO_BIN_EXE_jumpstarter-exec"))
            .arg(command)
            .output()
            .expect("failed to run jumpstarter-exec");
        assert_eq!(output.status.code(), Some(1), "{command}: {output:?}");
        assert!(output.stdout.is_empty(), "{command}: {output:?}");
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            stderr.contains("currently requires Unix"),
            "{command}: {stderr}"
        );
    }
}
