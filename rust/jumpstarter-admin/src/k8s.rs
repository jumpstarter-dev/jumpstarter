//! Kubernetes operations for the `jumpstarter.dev/v1alpha1` Client/Exporter/Lease
//! custom resources (`jumpstarter-kubernetes/{clients,exporters,leases}.py`).
//!
//! Uses kube-rs `DynamicObject` against the CRDs (no generated CRD types needed) and
//! the core `Secret` API to read issued credential tokens.

use std::collections::BTreeMap;
use std::time::Duration;

use base64::Engine;
use k8s_openapi::api::core::v1::{ConfigMap, Secret};
use kube::api::{
    Api, ApiResource, DeleteParams, DynamicObject, ListParams, ObjectMeta, PostParams, TypeMeta,
};
use kube::config::{KubeConfigOptions, Kubeconfig};
use kube::{Client, Config};

use crate::error::AdminError;

const POLL_ATTEMPTS: u32 = 20;
const POLL_DELAY: Duration = Duration::from_millis(500);

/// ConfigMap that holds the cluster's gRPC CA certificate
/// (`async_custom_object_api.py:CA_CONFIGMAP_NAME`).
const CA_CONFIGMAP_NAME: &str = "jumpstarter-service-ca-cert";

/// A connected admin client scoped to a namespace.
pub struct JumpstarterAdmin {
    client: Client,
    namespace: String,
}

/// The kind of Jumpstarter resource.
#[derive(Clone, Copy)]
pub enum Kind {
    Client,
    Exporter,
    Lease,
}

impl Kind {
    fn kind(self) -> &'static str {
        match self {
            Kind::Client => "Client",
            Kind::Exporter => "Exporter",
            Kind::Lease => "Lease",
        }
    }
    fn plural(self) -> &'static str {
        match self {
            Kind::Client => "clients",
            Kind::Exporter => "exporters",
            Kind::Lease => "leases",
        }
    }
    fn api_resource(self) -> ApiResource {
        ApiResource {
            group: "jumpstarter.dev".to_string(),
            version: "v1alpha1".to_string(),
            api_version: "jumpstarter.dev/v1alpha1".to_string(),
            kind: self.kind().to_string(),
            plural: self.plural().to_string(),
        }
    }
}

impl JumpstarterAdmin {
    /// Connect using the default kubeconfig (optionally a specific file/context).
    pub async fn connect(
        namespace: impl Into<String>,
        kubeconfig: Option<&str>,
        context: Option<&str>,
    ) -> Result<Self, AdminError> {
        let config = match kubeconfig {
            Some(path) => {
                let kc = Kubeconfig::read_from(path)
                    .map_err(|e| AdminError::Config(format!("reading kubeconfig '{path}': {e}")))?;
                Config::from_custom_kubeconfig(
                    kc,
                    &KubeConfigOptions {
                        context: context.map(String::from),
                        ..Default::default()
                    },
                )
                .await
                .map_err(|e| AdminError::Config(e.to_string()))?
            }
            None => Config::from_kubeconfig(&KubeConfigOptions {
                context: context.map(String::from),
                ..Default::default()
            })
            .await
            .map_err(|e| AdminError::Config(e.to_string()))?,
        };
        let client = Client::try_from(config)?;
        Ok(Self {
            client,
            namespace: namespace.into(),
        })
    }

    fn api(&self, kind: Kind) -> Api<DynamicObject> {
        Api::namespaced_with(self.client.clone(), &self.namespace, &kind.api_resource())
    }

    fn secrets(&self) -> Api<Secret> {
        Api::namespaced(self.client.clone(), &self.namespace)
    }

    /// The base64-encoded CA certificate bundle from the
    /// `jumpstarter-service-ca-cert` ConfigMap, used to populate `tls.ca` in
    /// generated configs (`async_custom_object_api.py:get_ca_bundle`). Returns
    /// an empty string when the ConfigMap is absent or has no `ca.crt`.
    ///
    /// Note: ConfigMap `data` is plain text (not base64 like a Secret), so the
    /// PEM is base64-encoded here to match the Python implementation.
    pub async fn ca_bundle(&self) -> Result<String, AdminError> {
        let api: Api<ConfigMap> = Api::namespaced(self.client.clone(), &self.namespace);
        let Some(cm) = api.get_opt(CA_CONFIGMAP_NAME).await? else {
            return Ok(String::new());
        };
        let ca_crt = cm
            .data
            .as_ref()
            .and_then(|d| d.get("ca.crt"))
            .map(String::as_str)
            .unwrap_or_default();
        if ca_crt.is_empty() {
            Ok(String::new())
        } else {
            Ok(base64::engine::general_purpose::STANDARD.encode(ca_crt.as_bytes()))
        }
    }

    /// Create a Client/Exporter resource and wait for its credential to be issued,
    /// returning the created object (`clients.py:create_client`).
    pub async fn create(
        &self,
        kind: Kind,
        name: &str,
        labels: BTreeMap<String, String>,
        oidc_username: Option<&str>,
    ) -> Result<DynamicObject, AdminError> {
        let mut data = serde_json::json!({});
        if let Some(username) = oidc_username {
            data["spec"] = serde_json::json!({ "username": username });
        }
        let obj = DynamicObject {
            types: Some(TypeMeta {
                api_version: "jumpstarter.dev/v1alpha1".to_string(),
                kind: kind.kind().to_string(),
            }),
            metadata: ObjectMeta {
                name: Some(name.to_string()),
                labels: (!labels.is_empty()).then_some(labels),
                ..Default::default()
            },
            data,
        };
        let api = self.api(kind);
        api.create(&PostParams::default(), &obj).await?;

        // Poll until the controller fills in status.credential.
        for _ in 0..POLL_ATTEMPTS {
            let current = api.get(name).await?;
            if current.data.pointer("/status/credential").is_some() {
                return Ok(current);
            }
            tokio::time::sleep(POLL_DELAY).await;
        }
        Err(AdminError::Other(format!(
            "timeout waiting for {} '{name}' credentials",
            kind.kind().to_lowercase()
        )))
    }

    pub async fn get(&self, kind: Kind, name: &str) -> Result<DynamicObject, AdminError> {
        Ok(self.api(kind).get(name).await?)
    }

    pub async fn list(&self, kind: Kind) -> Result<Vec<DynamicObject>, AdminError> {
        Ok(self.api(kind).list(&ListParams::default()).await?.items)
    }

    /// Delete a Client/Exporter and its credential secret. The controller garbage-collects the
    /// secret via an owner reference, but that is asynchronous; the Python admin deleted it
    /// explicitly, so callers (and the e2e) see it gone immediately.
    ///
    /// Order matters: delete the **object first**, then the secret. The controller reconciles a
    /// still-present object — for a connected exporter it would **recreate** the secret in the
    /// window between deleting the secret and deleting the object. Deleting the object first stops
    /// that reconciliation; the explicit secret delete then makes it gone synchronously
    /// (best-effort — it may already be GC'd, or the object may carry no credential).
    pub async fn delete(&self, kind: Kind, name: &str) -> Result<(), AdminError> {
        let secret = self.get(kind, name).await.ok().and_then(|obj| {
            obj.data
                .pointer("/status/credential/name")
                .and_then(|v| v.as_str())
                .map(String::from)
        });
        self.api(kind)
            .delete(name, &DeleteParams::default())
            .await?;
        if let Some(secret) = secret {
            let _ = self
                .secrets()
                .delete(&secret, &DeleteParams::default())
                .await;
        }
        Ok(())
    }

    /// The credential token + endpoint for a Client/Exporter
    /// (`clients.py:get_client_config`). The k8s `Secret.data` is already
    /// base64-decoded by k8s-openapi.
    pub async fn credentials(
        &self,
        kind: Kind,
        name: &str,
    ) -> Result<(String, String), AdminError> {
        let obj = self.get(kind, name).await?;
        let cred = obj
            .data
            .pointer("/status/credential/name")
            .and_then(|v| v.as_str())
            .ok_or_else(|| AdminError::Other(format!("{name} has no credential secret")))?;
        let endpoint = obj
            .data
            .pointer("/status/endpoint")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let token = self.read_token(cred).await?;
        Ok((endpoint, token))
    }

    async fn read_token(&self, secret_name: &str) -> Result<String, AdminError> {
        let secret = self.secrets().get(secret_name).await?;
        let bytes = secret
            .data
            .as_ref()
            .and_then(|d| d.get("token"))
            .ok_or_else(|| AdminError::Other(format!("secret '{secret_name}' has no token")))?;
        String::from_utf8(bytes.0.clone())
            .map_err(|e| AdminError::Other(format!("token is not valid UTF-8: {e}")))
    }

    /// Rotate a client's token by deleting its credential secret and waiting for the
    /// controller to regenerate it (`clients.py:rotate_client_token`).
    pub async fn rotate(&self, kind: Kind, name: &str) -> Result<String, AdminError> {
        let obj = self.get(kind, name).await?;
        let secret_name = obj
            .data
            .pointer("/status/credential/name")
            .and_then(|v| v.as_str())
            .ok_or_else(|| AdminError::Other(format!("{name} has no credential secret")))?
            .to_string();

        self.secrets()
            .delete(&secret_name, &DeleteParams::default())
            .await?;

        for _ in 0..POLL_ATTEMPTS {
            if let Ok(token) = self.read_token(&secret_name).await {
                return Ok(token);
            }
            tokio::time::sleep(POLL_DELAY).await;
        }
        Err(AdminError::Other(format!(
            "timeout waiting for '{name}' token regeneration"
        )))
    }
}
