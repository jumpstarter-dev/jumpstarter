//! Shared label and annotation constants for the `jumpstarter.dev/v1alpha1`
//! API group, ported from `controller/api/v1alpha1/groupversion_info.go` and
//! `controller/api/v1alpha1/lease_types.go`, plus the migration-annotation
//! override helper from `controller/api/v1alpha1/common_helpers.go`.

use std::collections::BTreeMap;

/// The API group used to register these objects.
// go: groupversion_info.go:29 (GroupVersion.Group)
pub const GROUP: &str = "jumpstarter.dev";

/// The API version used to register these objects.
// go: groupversion_info.go:29 (GroupVersion.Version)
pub const VERSION: &str = "v1alpha1";

/// AnnotationMigratedNamespace is the annotation key for migrated namespace
// go: groupversion_info.go:40
pub const ANNOTATION_MIGRATED_NAMESPACE: &str = "jumpstarter.dev/migrated-namespace";

/// AnnotationMigratedUID is the annotation key for migrated UID
// go: groupversion_info.go:43
pub const ANNOTATION_MIGRATED_UID: &str = "jumpstarter.dev/migrated-uid";

/// Label set on a Lease once it has ended (soft delete marker).
// go: lease_types.go:83 (LeaseLabelEnded)
pub const LEASE_LABEL_ENDED: &str = "jumpstarter.dev/lease-ended";

/// Value stored under [`LEASE_LABEL_ENDED`] for an ended lease.
// go: lease_types.go:84 (LeaseLabelEndedValue)
pub const LEASE_LABEL_ENDED_VALUE: &str = "true";

/// Prefix applied to user-supplied lease tags when they are stored as
/// ObjectMeta labels (reserving the bare `jumpstarter.dev/` namespace).
// go: lease_types.go:85 (LeaseTagMetadataPrefix)
pub const LEASE_TAG_METADATA_PREFIX: &str = "metadata.jumpstarter.dev/";

/// Returns the namespace and UID for an object, applying migration
/// annotation overrides if present.
///
/// A migration annotation must be present *and* non-empty to override the
/// object's own namespace/UID; empty values are ignored.
// go: common_helpers.go:7 (getNamespaceAndUID)
pub fn namespace_and_uid(
    namespace: &str,
    uid: &str,
    annotations: Option<&BTreeMap<String, String>>,
) -> (String, String) {
    let mut result_namespace = namespace;
    let mut result_uid = uid;

    if let Some(annotations) = annotations {
        if let Some(migrated_namespace) = annotations.get(ANNOTATION_MIGRATED_NAMESPACE) {
            if !migrated_namespace.is_empty() {
                result_namespace = migrated_namespace;
            }
        }
        if let Some(migrated_uid) = annotations.get(ANNOTATION_MIGRATED_UID) {
            if !migrated_uid.is_empty() {
                result_uid = migrated_uid;
            }
        }
    }

    (result_namespace.to_owned(), result_uid.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    const UID: &str = "123e4567-e89b-12d3-a456-426614174000";

    fn annotations(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    /// Table transliterated from `TestGetNamespaceAndUID`.
    // go: common_helpers_test.go:9
    #[test]
    fn test_namespace_and_uid() {
        struct Case {
            name: &'static str,
            annotations: Option<BTreeMap<String, String>>,
            expected_namespace: &'static str,
            expected_uid: &'static str,
        }

        let cases = [
            Case {
                name: "no annotations",
                annotations: None,
                expected_namespace: "default",
                expected_uid: UID,
            },
            Case {
                name: "empty annotations map",
                annotations: Some(BTreeMap::new()),
                expected_namespace: "default",
                expected_uid: UID,
            },
            Case {
                name: "migrated namespace only",
                annotations: Some(annotations(&[(
                    ANNOTATION_MIGRATED_NAMESPACE,
                    "migrated-ns",
                )])),
                expected_namespace: "migrated-ns",
                expected_uid: UID,
            },
            Case {
                name: "migrated uid only",
                annotations: Some(annotations(&[(
                    ANNOTATION_MIGRATED_UID,
                    "migrated-uid-value",
                )])),
                expected_namespace: "default",
                expected_uid: "migrated-uid-value",
            },
            Case {
                name: "both migrated namespace and uid",
                annotations: Some(annotations(&[
                    (ANNOTATION_MIGRATED_NAMESPACE, "migrated-ns"),
                    (ANNOTATION_MIGRATED_UID, "migrated-uid-value"),
                ])),
                expected_namespace: "migrated-ns",
                expected_uid: "migrated-uid-value",
            },
            Case {
                name: "empty migrated namespace value ignored",
                annotations: Some(annotations(&[(ANNOTATION_MIGRATED_NAMESPACE, "")])),
                expected_namespace: "default",
                expected_uid: UID,
            },
            Case {
                name: "empty migrated uid value ignored",
                annotations: Some(annotations(&[(ANNOTATION_MIGRATED_UID, "")])),
                expected_namespace: "default",
                expected_uid: UID,
            },
            Case {
                name: "both empty values ignored",
                annotations: Some(annotations(&[
                    (ANNOTATION_MIGRATED_NAMESPACE, ""),
                    (ANNOTATION_MIGRATED_UID, ""),
                ])),
                expected_namespace: "default",
                expected_uid: UID,
            },
            Case {
                name: "other annotations present",
                annotations: Some(annotations(&[
                    ("other.annotation/key", "value"),
                    (ANNOTATION_MIGRATED_NAMESPACE, "migrated-ns"),
                    ("another.annotation", "another-value"),
                ])),
                expected_namespace: "migrated-ns",
                expected_uid: UID,
            },
        ];

        for case in cases {
            let (got_namespace, got_uid) =
                namespace_and_uid("default", UID, case.annotations.as_ref());
            assert_eq!(
                got_namespace, case.expected_namespace,
                "namespace mismatch in case {:?}",
                case.name
            );
            assert_eq!(
                got_uid, case.expected_uid,
                "uid mismatch in case {:?}",
                case.name
            );
        }
    }
}
