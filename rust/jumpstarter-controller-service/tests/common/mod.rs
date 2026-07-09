//! Rust-driven envtest integration harness.
//!
//! Mirrors what controller-runtime's `envtest.Environment.Start()` does (see the
//! Go suite at `controller/internal/controller/suite_test.go`): it spawns a
//! standalone `etcd` + `kube-apiserver` pair from the `KUBEBUILDER_ASSETS`
//! binaries, waits for the apiserver to become ready, installs the four
//! `jumpstarter.dev` CRDs, and hands back a [`kube::Client`]. No kind cluster is
//! required.
//!
//! Auth model: the apiserver is started with `--authorization-mode=AlwaysAllow`
//! and anonymous auth (default), and serves a self-signed cert we do not verify
//! client-side (`accept_invalid_certs`). This keeps the harness ~200 lines while
//! remaining faithful to the reconciler behavior under test — the reconcilers
//! never depend on RBAC.
//!
//! The whole thing is env-gated: [`assets`] returns `None` when
//! `KUBEBUILDER_ASSETS` is unset, so `cargo test` stays hermetic by default.

#![allow(dead_code)]

use std::path::PathBuf;
use std::process::Stdio;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use k8s_openapi::api::core::v1::Namespace;
use k8s_openapi::apiextensions_apiserver::pkg::apis::apiextensions::v1::CustomResourceDefinition;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::ObjectMeta;
use kube::api::{Api, PostParams};
use kube::{Client, Config};
use tokio::process::{Child, Command};

/// The four CRDs the reconcilers operate on, read from the retained Go tree.
const CRD_FILES: &[&str] = &[
    "jumpstarter.dev_clients.yaml",
    "jumpstarter.dev_exporters.yaml",
    "jumpstarter.dev_leases.yaml",
    "jumpstarter.dev_exporteraccesspolicies.yaml",
];

/// Return `KUBEBUILDER_ASSETS` if it is set and non-empty. Callers must skip the
/// integration test entirely when this is `None`.
pub fn assets() -> Option<PathBuf> {
    match std::env::var("KUBEBUILDER_ASSETS") {
        Ok(dir) if !dir.is_empty() => Some(PathBuf::from(dir)),
        _ => None,
    }
}

/// Absolute path to `controller/deploy/operator/config/crd/bases`, relative to
/// this crate (`rust/jumpstarter-controller-core`).
fn crd_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../controller/deploy/operator/config/crd/bases")
}

/// Grab an ephemeral TCP port by binding to `:0` and immediately releasing it.
/// A small race window remains before the child re-binds; acceptable for tests.
fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

/// A running standalone control plane (`etcd` + `kube-apiserver`) plus a ready
/// [`Client`]. Dropping it kills both child processes and removes the temp dir.
pub struct TestEnv {
    tmp: PathBuf,
    etcd: Child,
    apiserver: Child,
    pub client: Client,
}

impl TestEnv {
    /// Boot etcd + kube-apiserver from `KUBEBUILDER_ASSETS`, wait for readiness
    /// (bounded), install the CRDs, and return a ready client. The whole
    /// sequence is bounded by an ~90s deadline so a broken apiserver surfaces as
    /// a diagnosable failure rather than a hang.
    pub async fn start() -> Result<TestEnv, Box<dyn std::error::Error>> {
        let assets = assets().ok_or("KUBEBUILDER_ASSETS not set")?;
        let etcd_bin = assets.join("etcd");
        let apiserver_bin = assets.join("kube-apiserver");

        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let tmp = std::env::temp_dir().join(format!("jmp-envtest-{nonce}"));
        let certs = tmp.join("certs");
        let etcd_data = tmp.join("etcd");
        std::fs::create_dir_all(&certs)?;
        std::fs::create_dir_all(&etcd_data)?;

        // --- Service-account signing material (rcgen) -----------------------
        // The apiserver requires an SA signing keypair even with the
        // ServiceAccount admission plugin disabled. A self-signed cert supplies
        // the public half (`--service-account-key-file` accepts a cert PEM); the
        // key pair's PKCS#8 PEM is the private signing key.
        let key_pair = rcgen::KeyPair::generate()?;
        let sa_params = rcgen::CertificateParams::new(vec!["kube-apiserver".to_string()])?;
        let sa_cert = sa_params.self_signed(&key_pair)?;
        let sa_pub = certs.join("sa.pub");
        let sa_key = certs.join("sa.key");
        std::fs::write(&sa_pub, sa_cert.pem())?;
        std::fs::write(&sa_key, key_pair.serialize_pem())?;

        // --- static-token auth ---------------------------------------------
        // The apiserver defaults to anonymous-auth disabled here, so we
        // authenticate an admin with a static bearer token (CSV:
        // token,user,uid,groups) and rely on `--authorization-mode=AlwaysAllow`.
        let token = format!("envtest-{nonce}");
        let token_csv = certs.join("token.csv");
        std::fs::write(
            &token_csv,
            format!("{token},admin,admin,\"system:masters\"\n"),
        )?;
        let token_file = certs.join("token"); // client reads the bare token
        std::fs::write(&token_file, &token)?;

        // --- etcd -----------------------------------------------------------
        let etcd_port = free_port();
        let peer_port = free_port();
        let etcd_client_url = format!("http://127.0.0.1:{etcd_port}");
        let peer_url = format!("http://127.0.0.1:{peer_port}");
        let etcd = Command::new(&etcd_bin)
            .args([
                "--name=default".to_string(),
                format!("--data-dir={}", etcd_data.display()),
                format!("--listen-client-urls={etcd_client_url}"),
                format!("--advertise-client-urls={etcd_client_url}"),
                format!("--listen-peer-urls={peer_url}"),
                format!("--initial-advertise-peer-urls={peer_url}"),
                format!("--initial-cluster=default={peer_url}"),
                "--unsafe-no-fsync".to_string(),
            ])
            .stdout(log_file(&tmp, "etcd.log"))
            .stderr(log_file(&tmp, "etcd.log"))
            .kill_on_drop(true)
            .spawn()?;

        // --- kube-apiserver -------------------------------------------------
        let api_port = free_port();
        let apiserver = Command::new(&apiserver_bin)
            .args([
                format!("--etcd-servers={etcd_client_url}"),
                format!("--secure-port={api_port}"),
                "--bind-address=127.0.0.1".to_string(),
                "--advertise-address=127.0.0.1".to_string(),
                format!("--cert-dir={}", certs.display()),
                format!("--service-account-key-file={}", sa_pub.display()),
                format!("--service-account-signing-key-file={}", sa_key.display()),
                "--service-account-issuer=https://kubernetes.default.svc.cluster.local".to_string(),
                "--api-audiences=https://kubernetes.default.svc.cluster.local".to_string(),
                format!("--token-auth-file={}", token_csv.display()),
                "--disable-admission-plugins=ServiceAccount".to_string(),
                "--service-cluster-ip-range=10.0.0.0/24".to_string(),
                "--authorization-mode=AlwaysAllow".to_string(),
                "--allow-privileged=true".to_string(),
            ])
            .stdout(log_file(&tmp, "apiserver.log"))
            .stderr(log_file(&tmp, "apiserver.log"))
            .kill_on_drop(true)
            .spawn()?;

        // --- client ---------------------------------------------------------
        let uri = format!("https://127.0.0.1:{api_port}")
            .parse::<http::Uri>()
            .unwrap();
        let mut config = Config::new(uri);
        config.accept_invalid_certs = true;
        config.default_namespace = "default".to_string();
        config.connect_timeout = Some(Duration::from_secs(5));
        config.auth_info.token_file = Some(token_file.to_string_lossy().into_owned());
        let client = Client::try_from(config)?;

        let env = TestEnv {
            tmp: tmp.clone(),
            etcd,
            apiserver,
            client: client.clone(),
        };

        // --- wait for readiness (bounded) -----------------------------------
        let deadline = Instant::now() + Duration::from_secs(90);
        let ns_api: Api<Namespace> = Api::all(client.clone());
        #[allow(unused_assignments)]
        let mut last_err = String::new();
        loop {
            match ns_api.list(&Default::default()).await {
                Ok(_) => break,
                Err(e) => last_err = e.to_string(),
            }
            if Instant::now() >= deadline {
                let log = std::fs::read_to_string(tmp.join("apiserver.log")).unwrap_or_default();
                let tail: String = log.lines().rev().take(25).collect::<Vec<_>>().join("\n");
                // Drop kills the children.
                drop(env);
                return Err(format!(
                    "kube-apiserver did not become ready within 90s (last client error: {last_err}).\n\
                     Last apiserver log lines:\n{tail}"
                )
                .into());
            }
            tokio::time::sleep(Duration::from_millis(300)).await;
        }

        // --- install CRDs ---------------------------------------------------
        env.install_crds().await?;

        Ok(env)
    }

    /// Read the four CRD YAMLs from the Go tree, create them, and wait for each
    /// to report the `Established` condition.
    async fn install_crds(&self) -> Result<(), Box<dyn std::error::Error>> {
        let dir = crd_dir();
        let api: Api<CustomResourceDefinition> = Api::all(self.client.clone());
        let mut names = Vec::new();
        for file in CRD_FILES {
            let yaml = std::fs::read_to_string(dir.join(file))
                .map_err(|e| format!("reading CRD {file}: {e}"))?;
            let crd: CustomResourceDefinition =
                serde_yaml_ng::from_str(&yaml).map_err(|e| format!("parsing CRD {file}: {e}"))?;
            let name = crd.metadata.name.clone().unwrap_or_default();
            api.create(&PostParams::default(), &crd)
                .await
                .map_err(|e| format!("creating CRD {name}: {e}"))?;
            names.push(name);
        }

        let deadline = Instant::now() + Duration::from_secs(30);
        for name in &names {
            loop {
                let crd = api.get(name).await?;
                let established = crd
                    .status
                    .as_ref()
                    .and_then(|s| s.conditions.as_ref())
                    .map(|cs| {
                        cs.iter()
                            .any(|c| c.type_ == "Established" && c.status == "True")
                    })
                    .unwrap_or(false);
                if established {
                    break;
                }
                if Instant::now() >= deadline {
                    return Err(format!("CRD {name} not Established within 30s").into());
                }
                tokio::time::sleep(Duration::from_millis(200)).await;
            }
        }
        Ok(())
    }

    /// Create a fresh namespace so each scenario is isolated (the lease
    /// scheduler reads *all* policies/leases in a namespace).
    pub async fn create_namespace(&self, name: &str) -> Result<(), kube::Error> {
        let api: Api<Namespace> = Api::all(self.client.clone());
        api.create(
            &PostParams::default(),
            &Namespace {
                metadata: ObjectMeta {
                    name: Some(name.to_string()),
                    ..Default::default()
                },
                ..Default::default()
            },
        )
        .await?;
        Ok(())
    }
}

impl Drop for TestEnv {
    fn drop(&mut self) {
        let _ = self.apiserver.start_kill();
        let _ = self.etcd.start_kill();
        let _ = std::fs::remove_dir_all(&self.tmp);
    }
}

fn log_file(tmp: &std::path::Path, name: &str) -> Stdio {
    match std::fs::File::create(tmp.join(name)) {
        Ok(f) => Stdio::from(f),
        Err(_) => Stdio::null(),
    }
}
