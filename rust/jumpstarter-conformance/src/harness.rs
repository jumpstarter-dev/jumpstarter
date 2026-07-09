//! Conformance harness: an envtest control plane plus the **real**
//! `ControllerService` + `ClientService`, driven in-process through their
//! generated tonic trait methods.
//!
//! This is the executable form of spec 02. The envtest bring-up
//! ([`TestEnv`]) is copied from the Phase-4/5 harness in
//! `jumpstarter-controller-{core,service}/tests/common/mod.rs`: it spawns a
//! standalone `etcd` + `kube-apiserver` pair from `KUBEBUILDER_ASSETS`, installs
//! the four `jumpstarter.dev` CRDs, and hands back a [`kube::Client`]. On top of
//! it, [`Harness`] wires the production services over that client with a **fixed
//! deterministic** signer/router key, a default [`TokenValidator`] (internal
//! prefix `internal:`, provisioning off), a [`ListenRegistry`], and a one-router
//! [`Router`] config — plus the CR/secret/token helpers every case needs.
//!
//! Env-gated: [`assets`] returns `None` when `KUBEBUILDER_ASSETS` is unset, so
//! the suite prints SKIP and stays hermetic. Every apiserver bring-up is bounded
//! at 90 s and every RPC helper at [`RPC_DEADLINE`] (10 s) — a wedged apiserver
//! or handler surfaces as a diagnosable failure, never a hang.

#![allow(dead_code)]

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use k8s_openapi::api::core::v1::{LocalObjectReference, Namespace, Secret};
use k8s_openapi::apiextensions_apiserver::pkg::apis::apiextensions::v1::CustomResourceDefinition;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{LabelSelector, ObjectMeta};
use kube::api::{Api, Patch, PatchParams, PostParams};
use kube::{Client, Config};
use serde_json::json;
use tokio::process::{Child, Command};
use tonic::metadata::MetadataMap;
use tonic::Request;

use jumpstarter_controller_api::client::{Client as ClientCr, ClientSpec};
use jumpstarter_controller_api::exporter::{Exporter, ExporterSpec};
use jumpstarter_controller_api::lease::{Lease, LeaseSpec};
use jumpstarter_controller_auth::signer::{Signer, INTERNAL_AUDIENCE, INTERNAL_ISSUER};
use jumpstarter_controller_auth::validator::TokenValidator;
use jumpstarter_controller_config::router::{Router, RouterEntry};
use jumpstarter_controller_config::types::Authentication;

use jumpstarter_controller_service::client_service::ClientService;
use jumpstarter_controller_service::controller_service::{ControllerAuth, ControllerService};
use jumpstarter_controller_service::listen_registry::ListenRegistry;

/// Every RPC helper is wrapped in this deadline (ANTI-STALL). Cases needing a
/// longer budget (the status-stream heartbeat lands at +10 s) override it with
/// their own [`deadline_for`] call.
pub const RPC_DEADLINE: Duration = Duration::from_secs(10);

/// Fixed signer seed — deterministic ES256 key so minted tokens are stable.
/// The Go differential leg passes this verbatim as `CONTROLLER_KEY` so both
/// implementations derive the identical ES256 key (token cross-compat proof).
pub const SIGNER_SEED: &[u8] = b"conformance-fixed-signer-key";
/// Fixed HS256 router key. The Go leg passes this as `ROUTER_KEY`.
pub const ROUTER_KEY: &[u8] = b"conformance-fixed-router-key";
/// The single router endpoint every ready exporter rendezvouses through.
pub const ROUTER_ENDPOINT: &str = "grpc://router-0.jumpstarter.example:443";

/// The four CRDs the services operate on, read from the retained Go tree.
const CRD_FILES: &[&str] = &[
    "jumpstarter.dev_clients.yaml",
    "jumpstarter.dev_exporters.yaml",
    "jumpstarter.dev_leases.yaml",
    "jumpstarter.dev_exporteraccesspolicies.yaml",
];

/// Return `KUBEBUILDER_ASSETS` if set and non-empty; callers skip when `None`.
pub fn assets() -> Option<PathBuf> {
    match std::env::var("KUBEBUILDER_ASSETS") {
        Ok(dir) if !dir.is_empty() => Some(PathBuf::from(dir)),
        _ => None,
    }
}

fn crd_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../controller/deploy/operator/config/crd/bases")
}

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

fn log_file(tmp: &std::path::Path, name: &str) -> Stdio {
    match std::fs::File::create(tmp.join(name)) {
        Ok(f) => Stdio::from(f),
        Err(_) => Stdio::null(),
    }
}

// ===========================================================================
// envtest control plane (copied from the service crate's tests/common/mod.rs)
// ===========================================================================

/// A running standalone control plane (`etcd` + `kube-apiserver`) plus a ready
/// [`Client`]. Dropping it kills both child processes and removes the temp dir.
pub struct TestEnv {
    tmp: PathBuf,
    etcd: Child,
    apiserver: Child,
    pub client: Client,
    /// Secure port the kube-apiserver is listening on (for the Go differential
    /// leg's kubeconfig).
    pub api_port: u16,
    /// The static bearer token that authenticates as `system:masters` against
    /// this apiserver (same token the [`Client`] uses).
    pub api_token: String,
}

impl TestEnv {
    /// Boot etcd + kube-apiserver from `KUBEBUILDER_ASSETS`, wait for readiness
    /// (bounded at ~90 s), install the CRDs, and return a ready client.
    pub async fn start() -> Result<TestEnv, Box<dyn std::error::Error>> {
        let assets = assets().ok_or("KUBEBUILDER_ASSETS not set")?;
        let etcd_bin = assets.join("etcd");
        let apiserver_bin = assets.join("kube-apiserver");

        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let tmp = std::env::temp_dir().join(format!("jmp-conformance-{nonce}"));
        let certs = tmp.join("certs");
        let etcd_data = tmp.join("etcd");
        std::fs::create_dir_all(&certs)?;
        std::fs::create_dir_all(&etcd_data)?;

        eprintln!("[harness] generating SA signing material");
        let key_pair = rcgen::KeyPair::generate()?;
        let sa_params = rcgen::CertificateParams::new(vec!["kube-apiserver".to_string()])?;
        let sa_cert = sa_params.self_signed(&key_pair)?;
        let sa_pub = certs.join("sa.pub");
        let sa_key = certs.join("sa.key");
        std::fs::write(&sa_pub, sa_cert.pem())?;
        std::fs::write(&sa_key, key_pair.serialize_pem())?;

        let token = format!("conformance-{nonce}");
        let token_csv = certs.join("token.csv");
        std::fs::write(
            &token_csv,
            format!("{token},admin,admin,\"system:masters\"\n"),
        )?;
        let token_file = certs.join("token");
        std::fs::write(&token_file, &token)?;

        eprintln!("[harness] spawning etcd");
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

        eprintln!("[harness] spawning kube-apiserver");
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
            api_port,
            api_token: token.clone(),
        };

        eprintln!("[harness] waiting for apiserver readiness (<=90s)");
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
                drop(env);
                return Err(format!(
                    "kube-apiserver did not become ready within 90s (last client error: {last_err}).\n\
                     Last apiserver log lines:\n{tail}"
                )
                .into());
            }
            tokio::time::sleep(Duration::from_millis(300)).await;
        }

        eprintln!("[harness] installing CRDs");
        env.install_crds().await?;
        eprintln!("[harness] control plane ready");

        Ok(env)
    }

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

    /// Write a kubeconfig pointing at this envtest apiserver (insecure TLS +
    /// the static `system:masters` token) so the Go conformance server
    /// subprocess can share the SAME control plane. Returns its path.
    pub fn write_kubeconfig(&self) -> Result<PathBuf, Box<dyn std::error::Error>> {
        let path = self.tmp.join("envtest.kubeconfig");
        let server = format!("https://127.0.0.1:{}", self.api_port);
        let token = &self.api_token;
        // NB: build with explicit "\n" (no `\` line-continuations) — a backslash
        // continuation would strip the leading whitespace of the next source
        // line and destroy the YAML indentation, yielding an empty config.
        let mut kubeconfig = String::new();
        for line in [
            "apiVersion: v1",
            "kind: Config",
            "clusters:",
            "- name: envtest",
            "  cluster:",
            &format!("    server: {server}"),
            "    insecure-skip-tls-verify: true",
            "contexts:",
            "- name: envtest",
            "  context:",
            "    cluster: envtest",
            "    user: envtest",
            "    namespace: default",
            "current-context: envtest",
            "users:",
            "- name: envtest",
            "  user:",
            &format!("    token: {token}"),
        ] {
            kubeconfig.push_str(line);
            kubeconfig.push('\n');
        }
        std::fs::write(&path, kubeconfig)?;
        Ok(path)
    }

    /// Create a fresh namespace so each scenario is isolated (the services read
    /// CR state per-namespace).
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

// ===========================================================================
// Service wiring + helpers
// ===========================================================================

/// Case result: `Ok(())` on pass, `Err(reason)` on a failed assertion.
pub type R = Result<(), String>;

/// The constructed service surface plus everything a case needs to arrange CR
/// state and mint tokens. `controller`/`client_svc` are behind `Arc` so a case
/// can `tokio::join!` a Dial with a concurrent status patch.
pub struct Harness {
    pub client: Client,
    pub signer: Arc<Signer>,
    pub validator: Arc<TokenValidator>,
    pub auth: Arc<ControllerAuth>,
    pub registry: Arc<ListenRegistry>,
    pub router: Router,
    pub controller: Arc<ControllerService>,
    pub client_svc: Arc<ClientService<Arc<ControllerAuth>>>,
}

impl Harness {
    /// Wire the production services over the envtest client with fixed keys, the
    /// default internal-prefix (`internal:`) validator, provisioning off, and a
    /// single label-less router (matches every exporter, score 0).
    pub fn new(kube: Client) -> Self {
        Self::with_provisioning(kube, false)
    }

    /// Same as [`Self::new`] but with Client auto-provisioning configurable.
    pub fn with_provisioning(kube: Client, provisioning: bool) -> Self {
        let signer = Arc::new(
            Signer::from_seed(SIGNER_SEED, INTERNAL_ISSUER, INTERNAL_AUDIENCE).expect("signer"),
        );
        let validator = Arc::new(
            TokenValidator::load(&Authentication::default(), signer.clone()).expect("validator"),
        );
        let auth = Arc::new(ControllerAuth::new(
            kube.clone(),
            validator.clone(),
            validator.internal_prefix().to_string(),
            provisioning,
        ));
        let registry = Arc::new(ListenRegistry::new());
        let router = single_router();

        let controller = Arc::new(ControllerService::new(
            kube.clone(),
            auth.clone(),
            registry.clone(),
            router.clone(),
            ROUTER_KEY.to_vec(),
        ));
        let client_svc = Arc::new(ClientService::new(
            kube.clone(),
            auth.clone(),
            10,
            signer.clone(),
        ));

        Self {
            client: kube,
            signer,
            validator,
            auth,
            registry,
            router,
            controller,
            client_svc,
        }
    }

    /// Build an alternate [`ControllerService`] over the same auth/registry but a
    /// caller-supplied router map (e.g. empty → `"no router available"`), sharing
    /// this harness's registry so listeners registered here are visible to it.
    pub fn controller_with_router(&self, router: Router) -> ControllerService {
        ControllerService::new(
            self.client.clone(),
            self.auth.clone(),
            self.registry.clone(),
            router,
            ROUTER_KEY.to_vec(),
        )
    }

    // -- token minting ------------------------------------------------------

    /// Mint a valid internal token whose `sub` is `subject`.
    pub fn token(&self, subject: &str) -> Result<String, String> {
        self.signer
            .token(subject)
            .map_err(|e| format!("mint token: {e}"))
    }

    /// Mint an internal token that is already expired (issued far enough in the
    /// past that `iat + lifetime < now`). Drives the `"token is expired"`
    /// substring re-auth contract.
    pub fn expired_token(&self, subject: &str) -> Result<String, String> {
        // Default lifetime is 365 days; issue two years in the past so exp is
        // comfortably behind the real clock.
        let two_years = 2 * 365 * 24 * 60 * 60;
        let issued_at = chrono::Utc::now().timestamp() - two_years;
        self.signer
            .token_at(subject, issued_at)
            .map_err(|e| format!("mint expired token: {e}"))
    }

    // -- CR + secret arrangement -------------------------------------------

    /// Create an Exporter CR and mint a valid internal token for it.
    pub async fn make_exporter(
        &self,
        ns: &str,
        name: &str,
        lbls: &[(&str, &str)],
    ) -> Result<(Exporter, String), String> {
        let api: Api<Exporter> = Api::namespaced(self.client.clone(), ns);
        let exp = Exporter {
            metadata: ObjectMeta {
                name: Some(name.to_string()),
                namespace: Some(ns.to_string()),
                labels: Some(labels(lbls)),
                ..Default::default()
            },
            spec: ExporterSpec::default(),
            status: None,
        };
        let created = api
            .create(&PostParams::default(), &exp)
            .await
            .map_err(|e| format!("create exporter {name}: {e}"))?;
        let token = self.token(&created.internal_subject())?;
        Ok((created, token))
    }

    /// Create a Client CR and mint a valid internal token for it.
    pub async fn make_client(&self, ns: &str, name: &str) -> Result<(ClientCr, String), String> {
        let api: Api<ClientCr> = Api::namespaced(self.client.clone(), ns);
        let c = ClientCr {
            metadata: ObjectMeta {
                name: Some(name.to_string()),
                namespace: Some(ns.to_string()),
                ..Default::default()
            },
            spec: ClientSpec::default(),
            status: None,
        };
        let created = api
            .create(&PostParams::default(), &c)
            .await
            .map_err(|e| format!("create client {name}: {e}"))?;
        let token = self.token(&created.internal_subject())?;
        Ok((created, token))
    }

    /// Create the `<client>-client` credential Secret that `RotateToken` patches.
    pub async fn make_client_secret(&self, ns: &str, client_name: &str) -> R {
        let api: Api<Secret> = Api::namespaced(self.client.clone(), ns);
        let mut data = BTreeMap::new();
        data.insert(
            "token".to_string(),
            k8s_openapi::ByteString(b"old-token".to_vec()),
        );
        api.create(
            &PostParams::default(),
            &Secret {
                metadata: ObjectMeta {
                    name: Some(format!("{client_name}-client")),
                    namespace: Some(ns.to_string()),
                    ..Default::default()
                },
                data: Some(data),
                ..Default::default()
            },
        )
        .await
        .map(|_| ())
        .map_err(|e| format!("create secret {client_name}-client: {e}"))
    }

    /// Set an exporter's `status.exporterStatus` on the status subresource.
    pub async fn set_exporter_status(&self, ns: &str, name: &str, status: &str) -> R {
        Api::<Exporter>::namespaced(self.client.clone(), ns)
            .patch_status(
                name,
                &PatchParams::default(),
                &Patch::Merge(json!({ "status": { "exporterStatus": status } })),
            )
            .await
            .map(|_| ())
            .map_err(|e| format!("set exporter status {name}={status}: {e}"))
    }

    /// Stamp an exporter's `status.leaseRef.name` (so ReportStatus release can
    /// find the held lease).
    pub async fn set_exporter_lease_ref(&self, ns: &str, name: &str, lease: &str) -> R {
        Api::<Exporter>::namespaced(self.client.clone(), ns)
            .patch_status(
                name,
                &PatchParams::default(),
                &Patch::Merge(json!({ "status": { "leaseRef": { "name": lease } } })),
            )
            .await
            .map(|_| ())
            .map_err(|e| format!("set exporter leaseRef {name}={lease}: {e}"))
    }

    /// Create a Lease and stamp `status.exporterRef.name` so it reads as
    /// assigned to `exporter_name`.
    pub async fn create_assigned_lease(
        &self,
        ns: &str,
        name: &str,
        client_name: &str,
        exporter_name: &str,
        selector: &[(&str, &str)],
    ) -> R {
        let api: Api<Lease> = Api::namespaced(self.client.clone(), ns);
        let mut lease = Lease::new(
            name,
            LeaseSpec {
                client_ref: LocalObjectReference {
                    name: client_name.to_string(),
                },
                selector: match_labels_selector(selector),
                ..Default::default()
            },
        );
        lease.metadata.namespace = Some(ns.to_string());
        api.create(&PostParams::default(), &lease)
            .await
            .map_err(|e| format!("create lease {name}: {e}"))?;
        // `status.ended` is required on the Lease status subresource.
        api.patch_status(
            name,
            &PatchParams::default(),
            &Patch::Merge(json!({
                "status": { "ended": false, "exporterRef": { "name": exporter_name } }
            })),
        )
        .await
        .map(|_| ())
        .map_err(|e| format!("stamp lease {name} exporterRef: {e}"))
    }

    /// Create a Lease WITHOUT stamping `status.exporterRef` — reads as
    /// not-yet-active (Dial → `"lease not active"`).
    pub async fn create_unassigned_lease(&self, ns: &str, name: &str, client_name: &str) -> R {
        let api: Api<Lease> = Api::namespaced(self.client.clone(), ns);
        // A non-empty selector is required by the Lease CEL rule (`one of
        // selector or exporterRef.name is required`); the point of this helper is
        // to leave `status.exporterRef` unset, not `spec.selector`.
        let mut lease = Lease::new(
            name,
            LeaseSpec {
                client_ref: LocalObjectReference {
                    name: client_name.to_string(),
                },
                selector: match_labels_selector(&[("dut", "a")]),
                ..Default::default()
            },
        );
        lease.metadata.namespace = Some(ns.to_string());
        api.create(&PostParams::default(), &lease)
            .await
            .map(|_| ())
            .map_err(|e| format!("create unassigned lease {name}: {e}"))
    }

    pub fn leases(&self, ns: &str) -> Api<Lease> {
        Api::namespaced(self.client.clone(), ns)
    }
    pub fn exporters(&self, ns: &str) -> Api<Exporter> {
        Api::namespaced(self.client.clone(), ns)
    }
    pub fn secrets(&self, ns: &str) -> Api<Secret> {
        Api::namespaced(self.client.clone(), ns)
    }
}

// ===========================================================================
// free helpers
// ===========================================================================

/// One router with no labels → matches every exporter (score 0).
pub fn single_router() -> Router {
    let mut router: Router = BTreeMap::new();
    router.insert(
        "router-0".to_string(),
        RouterEntry {
            endpoint: ROUTER_ENDPOINT.to_string(),
            labels: BTreeMap::new(),
        },
    );
    router
}

pub fn labels(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

pub fn match_labels_selector(pairs: &[(&str, &str)]) -> LabelSelector {
    LabelSelector {
        match_labels: Some(labels(pairs)),
        match_expressions: None,
    }
}

/// Attach bearer + jumpstarter attribute metadata to a request's metadata map.
pub fn attach_metadata(md: &mut MetadataMap, token: &str, ns: &str, kind: &str, name: &str) {
    md.insert("authorization", format!("Bearer {token}").parse().unwrap());
    md.insert("jumpstarter-namespace", ns.parse().unwrap());
    md.insert("jumpstarter-kind", kind.parse().unwrap());
    md.insert("jumpstarter-name", name.parse().unwrap());
}

/// Build a tonic request with full bearer + attribute metadata.
pub fn request<T>(payload: T, token: &str, ns: &str, kind: &str, name: &str) -> Request<T> {
    let mut req = Request::new(payload);
    attach_metadata(req.metadata_mut(), token, ns, kind, name);
    req
}

/// Build a tonic request whose metadata is customized by `f` (for the auth
/// edge cases: missing/duplicate headers, omitted keys).
pub fn request_with<T>(payload: T, f: impl FnOnce(&mut MetadataMap)) -> Request<T> {
    let mut req = Request::new(payload);
    f(req.metadata_mut());
    req
}

pub fn want(cond: bool, msg: impl Into<String>) -> R {
    if cond {
        Ok(())
    } else {
        Err(msg.into())
    }
}

/// Wrap a future in [`RPC_DEADLINE`] (ANTI-STALL).
pub async fn deadline<F, T>(what: &str, fut: F) -> Result<T, String>
where
    F: std::future::Future<Output = T>,
{
    tokio::time::timeout(RPC_DEADLINE, fut)
        .await
        .map_err(|_| format!("{what}: timed out after {RPC_DEADLINE:?}"))
}

/// Wrap a future in an explicit deadline (for the heartbeat case that needs a
/// budget slightly over 10 s).
pub async fn deadline_for<F, T>(what: &str, budget: Duration, fut: F) -> Result<T, String>
where
    F: std::future::Future<Output = T>,
{
    tokio::time::timeout(budget, fut)
        .await
        .map_err(|_| format!("{what}: timed out after {budget:?}"))
}
