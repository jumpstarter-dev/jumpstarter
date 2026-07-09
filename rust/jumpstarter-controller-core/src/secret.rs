//! Credential-secret reconciliation, porting
//! `controller/internal/controller/secret_helpers.go` (`ensureSecret`).
//!
//! Each `Client` / `Exporter` owns a credential `Secret` holding a signed
//! internal token under the `"token"` key. `ensure_secret` is idempotent: it
//! creates the secret if missing, and otherwise only re-mints the token when it
//! is absent or no longer validates — matching the Go regeneration condition
//! `!ok || signer.Validate(token) != nil` exactly.

use std::collections::BTreeMap;

use jumpstarter_controller_auth::signer::{Signer, SignerError};
use k8s_openapi::api::core::v1::Secret;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{ObjectMeta, OwnerReference};
use k8s_openapi::ByteString;
use kube::api::{Api, Patch, PatchParams, PostParams};
use kube::{Client, Resource};

/// The `Secret.data` key holding the signed token.
// go: secret_helpers.go:16 (TokenKey)
pub const TOKEN_KEY: &str = "token";

/// Errors from [`ensure_secret`].
#[derive(Debug, thiserror::Error)]
pub enum SecretError {
    /// A kube API call (get/create/patch) failed.
    #[error("secret api call failed: {0}")]
    Kube(#[from] kube::Error),
    /// The token could not be signed.
    #[error("failed to sign token: {0}")]
    Signer(#[from] SignerError),
    /// The owner object lacks the metadata (name/uid) needed to build a
    /// controller owner reference — the port of `SetControllerReference`
    /// returning an error.
    #[error("owner object has no name/uid for controller owner reference")]
    OwnerReference,
}

/// Port of `ensureSecret` (`secret_helpers.go:18`).
///
/// Ensures the credential `Secret` `<namespace>/<name>` exists, is owned by
/// `owner`, and carries a valid token for `subject`. Returns the resulting
/// secret (server state after create/patch).
///
/// * absent ⇒ mint a token, build the `Opaque` secret with the controller
///   owner reference, and `create` it;
/// * present ⇒ (re)assert the controller owner reference and, **only if** the
///   token is missing or fails `signer.validate`, mint a new one; then `patch`
///   (JSON merge, matching Go's `client.MergeFrom`).
pub async fn ensure_secret<O>(
    client: &Client,
    namespace: &str,
    name: &str,
    signer: &Signer,
    subject: &str,
    owner: &O,
) -> Result<Secret, SecretError>
where
    O: Resource<DynamicType = ()>,
{
    let owner_ref = owner
        .controller_owner_ref(&())
        .ok_or(SecretError::OwnerReference)?;
    let api: Api<Secret> = Api::namespaced(client.clone(), namespace);

    match api.get_opt(name).await? {
        None => {
            // go: secret_helpers.go:35-59 (not present, create)
            let token = signer.token(subject)?;
            let secret = credential_secret(namespace, name, &token, owner_ref);
            Ok(api.create(&PostParams::default(), &secret).await?)
        }
        Some(existing) => {
            // go: secret_helpers.go:60-84 (present, patch)
            let mut owner_references = existing
                .metadata
                .owner_references
                .clone()
                .unwrap_or_default();
            upsert_controller_ref(&mut owner_references, owner_ref);

            let mut patch = Secret {
                metadata: ObjectMeta {
                    owner_references: Some(owner_references),
                    ..Default::default()
                },
                ..Default::default()
            };

            if token_needs_refresh(existing.data.as_ref(), signer) {
                let token = signer.token(subject)?;
                patch.data = Some(token_data(&token));
            }

            Ok(api
                .patch(name, &PatchParams::default(), &Patch::Merge(&patch))
                .await?)
        }
    }
}

/// Build the credential `Secret` object created for a not-yet-present owner.
///
/// Split out from [`ensure_secret`] so the shape (type `Opaque`, token under
/// [`TOKEN_KEY`], controller owner reference) is testable without a cluster.
// go: secret_helpers.go:41-54
pub fn credential_secret(
    namespace: &str,
    name: &str,
    token: &str,
    owner_ref: OwnerReference,
) -> Secret {
    Secret {
        metadata: ObjectMeta {
            name: Some(name.to_string()),
            namespace: Some(namespace.to_string()),
            owner_references: Some(vec![owner_ref]),
            ..Default::default()
        },
        type_: Some("Opaque".to_string()),
        data: Some(token_data(token)),
        ..Default::default()
    }
}

/// The `{ "token": <bytes> }` data map.
fn token_data(token: &str) -> BTreeMap<String, ByteString> {
    BTreeMap::from([(TOKEN_KEY.to_string(), ByteString(token.as_bytes().to_vec()))])
}

/// Port of the Go regeneration guard `!ok || signer.Validate(token) != nil`:
/// the token must be re-minted when the key is absent, not valid UTF-8, or no
/// longer passes `signer.validate`.
// go: secret_helpers.go:66-67
pub fn token_needs_refresh(data: Option<&BTreeMap<String, ByteString>>, signer: &Signer) -> bool {
    match data.and_then(|d| d.get(TOKEN_KEY)) {
        Some(ByteString(bytes)) => match std::str::from_utf8(bytes) {
            Ok(token) => signer.validate(token).is_err(),
            Err(_) => true,
        },
        None => true,
    }
}

/// Emulate `controllerutil.SetControllerReference`'s upsert: replace any
/// existing owner reference with the same UID, otherwise append. Preserves any
/// other (non-controller) owner references already on the secret.
fn upsert_controller_ref(refs: &mut Vec<OwnerReference>, owner_ref: OwnerReference) {
    if let Some(slot) = refs.iter_mut().find(|r| r.uid == owner_ref.uid) {
        *slot = owner_ref;
    } else {
        refs.push(owner_ref);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_controller_api::exporter::{Exporter, ExporterSpec};
    use k8s_openapi::apimachinery::pkg::apis::meta::v1::ObjectMeta as MetaObjectMeta;

    const ISSUER: &str = "https://jumpstarter.dev/oidc";
    const AUDIENCE: &str = "https://jumpstarter.dev/controller";
    const UID: &str = "123e4567-e89b-12d3-a456-426614174000";

    fn signer() -> Signer {
        Signer::from_seed(b"unit-test-seed", ISSUER, AUDIENCE).unwrap()
    }

    fn sample_exporter() -> Exporter {
        Exporter {
            metadata: MetaObjectMeta {
                name: Some("my-exporter".into()),
                namespace: Some("default".into()),
                uid: Some(UID.into()),
                ..Default::default()
            },
            spec: ExporterSpec::default(),
            status: None,
        }
    }

    /// Decode a JWT's `sub` claim without verifying (payload is the middle,
    /// base64url-no-pad segment).
    fn jwt_subject(token: &str) -> String {
        use base64::Engine;
        let payload_b64 = token.split('.').nth(1).expect("jwt has 3 segments");
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(payload_b64)
            .expect("payload is base64url");
        let claims: serde_json::Value = serde_json::from_slice(&payload).unwrap();
        claims["sub"].as_str().unwrap().to_string()
    }

    // The minted token's subject must be the owner's InternalSubject.
    #[test]
    fn credential_secret_token_carries_internal_subject() {
        let signer = signer();
        let exporter = sample_exporter();
        let subject = exporter.internal_subject();
        assert_eq!(subject, format!("exporter:default:my-exporter:{UID}"));

        let token = signer.token(&subject).unwrap();
        let owner_ref = {
            use kube::Resource;
            exporter.controller_owner_ref(&()).unwrap()
        };
        let secret = credential_secret("default", "my-exporter-exporter", &token, owner_ref);

        // shape
        assert_eq!(
            secret.metadata.name.as_deref(),
            Some("my-exporter-exporter")
        );
        assert_eq!(secret.metadata.namespace.as_deref(), Some("default"));
        assert_eq!(secret.type_.as_deref(), Some("Opaque"));
        let owners = secret.metadata.owner_references.as_ref().unwrap();
        assert_eq!(owners.len(), 1);
        assert_eq!(owners[0].kind, "Exporter");
        assert_eq!(owners[0].controller, Some(true));

        // token key + subject
        let stored = &secret.data.as_ref().unwrap()[TOKEN_KEY];
        let stored_token = std::str::from_utf8(&stored.0).unwrap();
        assert!(signer.validate(stored_token).is_ok());
        assert_eq!(jwt_subject(stored_token), subject);
    }

    #[test]
    fn token_needs_refresh_when_absent() {
        assert!(token_needs_refresh(None, &signer()));
        assert!(token_needs_refresh(Some(&BTreeMap::new()), &signer()));
    }

    #[test]
    fn token_needs_refresh_when_invalid_but_not_when_valid() {
        let signer = signer();
        let valid = signer.token("exporter:default:e:uid").unwrap();
        assert!(!token_needs_refresh(Some(&token_data(&valid)), &signer));

        // A token minted by a *different* key must be treated as invalid.
        let other = Signer::from_seed(b"a-different-seed", ISSUER, AUDIENCE).unwrap();
        let foreign = other.token("exporter:default:e:uid").unwrap();
        assert!(token_needs_refresh(Some(&token_data(&foreign)), &signer));

        // Non-UTF-8 bytes ⇒ refresh.
        let garbage = BTreeMap::from([(TOKEN_KEY.to_string(), ByteString(vec![0xff, 0xfe]))]);
        assert!(token_needs_refresh(Some(&garbage), &signer));
    }

    #[test]
    fn upsert_controller_ref_replaces_same_uid_and_keeps_others() {
        let other = OwnerReference {
            api_version: "v1".into(),
            kind: "Foo".into(),
            name: "foo".into(),
            uid: "other-uid".into(),
            ..Default::default()
        };
        let old = OwnerReference {
            api_version: "jumpstarter.dev/v1alpha1".into(),
            kind: "Exporter".into(),
            name: "e".into(),
            uid: UID.into(),
            controller: Some(true),
            ..Default::default()
        };
        let mut refs = vec![other.clone(), old];
        let new = OwnerReference {
            api_version: "jumpstarter.dev/v1alpha1".into(),
            kind: "Exporter".into(),
            name: "e-renamed".into(),
            uid: UID.into(),
            controller: Some(true),
            block_owner_deletion: Some(true),
        };
        upsert_controller_ref(&mut refs, new.clone());
        assert_eq!(refs.len(), 2);
        assert_eq!(refs[0], other); // untouched
        assert_eq!(refs[1], new); // replaced by uid
    }
}
