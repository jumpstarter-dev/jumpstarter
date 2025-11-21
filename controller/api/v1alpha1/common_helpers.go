package v1alpha1

import "k8s.io/apimachinery/pkg/types"

// getNamespaceAndUID returns the namespace and UID for an object, applying migration
// annotation overrides if present.
func getNamespaceAndUID(namespace string, uid types.UID, annotations map[string]string) (string, string) {
	resultNamespace := namespace
	resultUID := string(uid)

	if annotations != nil {
		if migratedNamespace, ok := annotations[AnnotationMigratedNamespace]; ok && migratedNamespace != "" {
			resultNamespace = migratedNamespace
		}
		if migratedUID, ok := annotations[AnnotationMigratedUID]; ok && migratedUID != "" {
			resultUID = migratedUID
		}
	}

	return resultNamespace, resultUID
}

