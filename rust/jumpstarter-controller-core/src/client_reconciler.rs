//! The Client reconciler, porting
//! `controller/internal/controller/client_controller.go`.
//!
//! Per reconcile pass, for a `Client`:
//!
//!   1. ensure the credential `Secret` `<name>-client` (owner-referenced);
//!   2. set `status.endpoint = controllerEndpoint()`;
//!   3. patch the status subresource, then emit `CredentialCreated` **only**
//!      when the credential reference went from unset to set;
//!   4. a 409 conflict requeues immediately with no error.
//!
//! No finalizer — the credential `Secret` is garbage-collected via its owner
//! reference. `For(Client)`, `Owns(Secret)`.

use std::sync::Arc;
use std::time::Duration;

use futures::StreamExt;
use jumpstarter_controller_api::client::Client as ClientCr;
use jumpstarter_controller_auth::signer::Signer;
use k8s_openapi::api::core::v1::{LocalObjectReference, Secret};
use kube::api::{Api, Patch, PatchParams};
use kube::runtime::controller::{Action, Controller};
use kube::runtime::events::{Event, EventType, Recorder, Reporter};
use kube::runtime::watcher;
use kube::{Client as KubeClient, Resource, ResourceExt};

use crate::exporter_reconciler::controller_endpoint;
use crate::secret::{ensure_secret, SecretError};

/// Reconciler dependencies shared across reconcile passes.
pub struct Context {
    /// Kube client.
    pub client: KubeClient,
    /// The internal ES256 signer minting credential tokens.
    pub signer: Arc<Signer>,
    /// Event recorder (`events.k8s.io`).
    pub recorder: Recorder,
}

/// Errors surfaced from a reconcile pass.
#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    /// A kube API call failed.
    #[error("kube api error: {0}")]
    Kube(#[from] kube::Error),
    /// Credential-secret reconciliation failed.
    #[error(transparent)]
    Secret(#[from] SecretError),
}

fn is_conflict(err: &kube::Error) -> bool {
    matches!(err, kube::Error::Api(response) if response.code == 409)
}

/// Reconcile a single `Client`. Port of `ClientReconciler.Reconcile`.
pub async fn reconcile(client: Arc<ClientCr>, ctx: Arc<Context>) -> Result<Action, ClientError> {
    let namespace = client.namespace().unwrap_or_default();
    let name = client.name_any();
    let mut client = (*client).clone();

    // Go compares the credential ref before and after to gate the event.
    let prev_credential = client.status.as_ref().and_then(|s| s.credential.clone());

    // 1. credential secret <name>-client
    let secret_name = format!("{name}-client");
    let subject = client.internal_subject();
    let secret = ensure_secret(
        &ctx.client,
        &namespace,
        &secret_name,
        &ctx.signer,
        &subject,
        &client,
    )
    .await?;
    let status = client.status.get_or_insert_with(Default::default);
    status.credential = Some(LocalObjectReference {
        name: secret.name_any(),
    });

    // 2. endpoint
    status.endpoint = controller_endpoint();

    let new_credential = client.status.as_ref().and_then(|s| s.credential.clone());

    // 3. status patch (merge), then event only on success.
    let api: Api<ClientCr> = Api::namespaced(ctx.client.clone(), &namespace);
    let patch = serde_json::json!({ "status": client.status });
    match api
        .patch_status(&name, &PatchParams::default(), &Patch::Merge(&patch))
        .await
    {
        Ok(_) => {}
        Err(err) if is_conflict(&err) => return Ok(Action::requeue(Duration::ZERO)),
        Err(err) => return Err(err.into()),
    }

    // go: client_controller.go:75-78 (emit only on nil -> non-nil transition)
    if prev_credential.is_none() {
        if let Some(credential) = new_credential {
            if let Err(err) = ctx
                .recorder
                .publish(
                    &Event {
                        type_: EventType::Normal,
                        reason: "CredentialCreated".to_string(),
                        note: Some(format!(
                            "Credential secret created for client: secret={}",
                            credential.name
                        )),
                        action: "CredentialCreated".to_string(),
                        secondary: None,
                    },
                    &client.object_ref(&()),
                )
                .await
            {
                tracing::warn!(%name, error = %err, "failed to publish client event");
            }
        }
    }

    Ok(Action::await_change())
}

fn error_policy(_client: Arc<ClientCr>, _err: &ClientError, _ctx: Arc<Context>) -> Action {
    Action::requeue(Duration::from_secs(5))
}

/// Build and run the Client controller: `For(Client)`, `Owns(Secret)`.
pub async fn run(client: KubeClient, signer: Arc<Signer>, namespace: String) {
    let reporter = Reporter {
        controller: "client-controller".into(),
        instance: std::env::var("CONTROLLER_POD_NAME").ok(),
    };
    let context = Arc::new(Context {
        client: client.clone(),
        signer,
        recorder: Recorder::new(client.clone(), reporter),
    });

    Controller::new(
        Api::<ClientCr>::namespaced(client.clone(), &namespace),
        watcher::Config::default(),
    )
    .owns(
        Api::<Secret>::namespaced(client.clone(), &namespace),
        watcher::Config::default(),
    )
    .run(reconcile, error_policy, context)
    .for_each(|_| async {})
    .await;
}
