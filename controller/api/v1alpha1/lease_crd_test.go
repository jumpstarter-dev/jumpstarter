package v1alpha1

import (
	"os"
	"testing"

	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	"sigs.k8s.io/yaml"
)

const leaseCRDPath = "../../deploy/operator/config/crd/bases/jumpstarter.dev_leases.yaml"

func loadSharedWithSchema(t *testing.T) apiextensionsv1.JSONSchemaProps {
	t.Helper()
	data, err := os.ReadFile(leaseCRDPath)
	if err != nil {
		t.Fatalf("reading generated lease CRD: %v", err)
	}
	var crd apiextensionsv1.CustomResourceDefinition
	if err := yaml.Unmarshal(data, &crd); err != nil {
		t.Fatalf("parsing generated lease CRD: %v", err)
	}
	for _, version := range crd.Spec.Versions {
		if version.Name == GroupVersion.Version {
			return version.Schema.OpenAPIV3Schema.Properties["spec"].Properties["sharedWith"]
		}
	}
	t.Fatalf("generated lease CRD has no %s version", GroupVersion.Version)
	return apiextensionsv1.JSONSchemaProps{}
}

func TestLeaseCRD_SharedWithMaxItemsMatchesMaxSharedWithEntries(t *testing.T) {
	sharedWith := loadSharedWithSchema(t)
	if sharedWith.MaxItems == nil {
		t.Fatal("sharedWith has no maxItems")
	}
	if got := *sharedWith.MaxItems; got != MaxSharedWithEntries {
		t.Errorf("sharedWith maxItems is %d, MaxSharedWithEntries is %d", got, MaxSharedWithEntries)
	}
}
