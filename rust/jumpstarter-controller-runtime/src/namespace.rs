//! Watch-namespace resolution, ported from `getWatchNamespace` in
//! `controller/cmd/main.go`.
//!
//! The controller only supports operating on a single namespace (since
//! 0.8.0); the namespace is resolved from multiple sources in order and
//! resolution failure is fatal at startup, exactly like the Go binary.

use std::path::Path;

/// Path to the namespace file mounted by Kubernetes in every pod.
///
/// Go: `namespaceFile` constant in `controller/cmd/main.go`.
pub const NAMESPACE_FILE: &str = "/var/run/secrets/kubernetes.io/serviceaccount/namespace";

/// Name of the environment variable holding the explicit namespace override
/// (re-exported from the shared config crate's env-var constants).
pub use jumpstarter_controller_config::env::NAMESPACE as NAMESPACE_ENV;

/// No namespace could be resolved. The message carries the same intent as
/// the fatal `setupLog.Error` in Go's `main()` (which then `os.Exit(1)`s):
/// "Jumpstarter controller can only be configured to work on a single
/// namespace since 0.8.0".
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("Jumpstarter controller can only be configured to work on a single namespace since 0.8.0")]
pub struct SingleNamespaceRequired;

/// Returns the namespace the controller should watch.
///
/// It tries multiple sources in order:
/// 1. `NAMESPACE` environment variable (explicit configuration takes
///    precedence)
/// 2. Namespace file (automatically mounted by Kubernetes in every pod)
/// 3. Error (empty namespace is not supported since 0.8.0)
///
/// Like Go's `os.ReadFile` + `string(ns)`, the file content is used as-is
/// (no whitespace trimming).
pub fn get_watch_namespace() -> Result<String, SingleNamespaceRequired> {
    resolve(
        std::env::var(NAMESPACE_ENV).ok().as_deref(),
        Path::new(NAMESPACE_FILE),
    )
}

/// Dependency-injected core of [`get_watch_namespace`]; exposed for tests.
///
/// `env_namespace` is the raw value of the `NAMESPACE` environment variable
/// (`None` when unset; Go's `os.Getenv` conflates unset and empty, and both
/// fall through here).
pub fn resolve(
    env_namespace: Option<&str>,
    namespace_file: &Path,
) -> Result<String, SingleNamespaceRequired> {
    // First check the NAMESPACE environment variable (explicit configuration).
    if let Some(ns) = env_namespace {
        if !ns.is_empty() {
            tracing::info!(
                namespace = ns,
                "Using namespace from NAMESPACE environment variable"
            );
            return Ok(ns.to_string());
        }
    }

    // Fall back to reading from the namespace file mounted by Kubernetes.
    if let Ok(bytes) = std::fs::read(namespace_file) {
        // Go converts the raw bytes with string(ns); mirror that leniently.
        let namespace = String::from_utf8_lossy(&bytes).into_owned();
        if !namespace.is_empty() {
            tracing::info!(
                namespace = namespace.as_str(),
                "Auto-detected namespace from service account"
            );
            return Ok(namespace);
        }
    }

    Err(SingleNamespaceRequired)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// A path guaranteed not to exist (mirrors running outside a pod).
    fn missing_file() -> PathBuf {
        PathBuf::from("/nonexistent/jumpstarter-controller-runtime/namespace")
    }

    /// Writes `content` to a unique temp file and returns its path.
    fn temp_namespace_file(name: &str, content: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "jumpstarter-controller-runtime-ns-test-{}-{}",
            std::process::id(),
            name
        ));
        std::fs::write(&path, content).expect("write temp namespace file");
        path
    }

    #[test]
    fn env_takes_precedence() {
        let file = temp_namespace_file("env-precedence", "file-ns");
        assert_eq!(
            resolve(Some("env-ns"), &file),
            Ok("env-ns".to_string()),
            "explicit NAMESPACE env must win over the mounted file"
        );
        std::fs::remove_file(&file).ok();
    }

    #[test]
    fn empty_env_falls_through_to_file() {
        // Go's os.Getenv returns "" for unset too; both fall through.
        let file = temp_namespace_file("empty-env", "file-ns");
        assert_eq!(resolve(Some(""), &file), Ok("file-ns".to_string()));
        assert_eq!(resolve(None, &file), Ok("file-ns".to_string()));
        std::fs::remove_file(&file).ok();
    }

    #[test]
    fn file_content_is_not_trimmed() {
        // Go uses string(ns) with no TrimSpace; preserve that exactly.
        let file = temp_namespace_file("untrimmed", "my-ns\n");
        assert_eq!(resolve(None, &file), Ok("my-ns\n".to_string()));
        std::fs::remove_file(&file).ok();
    }

    #[test]
    fn empty_file_is_fatal() {
        let file = temp_namespace_file("empty-file", "");
        assert_eq!(resolve(None, &file), Err(SingleNamespaceRequired));
        std::fs::remove_file(&file).ok();
    }

    #[test]
    fn nothing_resolvable_is_fatal() {
        let err = resolve(None, &missing_file()).unwrap_err();
        assert_eq!(
            err.to_string(),
            "Jumpstarter controller can only be configured to work on a single namespace since 0.8.0"
        );
    }

    #[test]
    fn get_watch_namespace_honors_env_override() {
        // This is the only test in the crate touching the NAMESPACE process
        // env; both scenarios run inside one test to avoid interleaving.
        let saved = std::env::var(NAMESPACE_ENV).ok();

        std::env::set_var(NAMESPACE_ENV, "override-ns");
        assert_eq!(get_watch_namespace(), Ok("override-ns".to_string()));

        std::env::remove_var(NAMESPACE_ENV);
        // Outside a pod (no mounted namespace file) resolution must fail.
        if !Path::new(NAMESPACE_FILE).exists() {
            assert_eq!(get_watch_namespace(), Err(SingleNamespaceRequired));
        }

        match saved {
            Some(value) => std::env::set_var(NAMESPACE_ENV, value),
            None => std::env::remove_var(NAMESPACE_ENV),
        }
    }
}
