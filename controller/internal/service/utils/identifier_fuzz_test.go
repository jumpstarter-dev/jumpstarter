package utils

import (
	"strings"
	"testing"

	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

func FuzzParseUnparseExporterIdentifier(f *testing.F) {
	f.Add("default", "my-exporter")
	f.Add("jumpstarter-lab", "test-exporter-hooks")
	f.Add("ns", "name")
	f.Add("", "")

	f.Fuzz(func(t *testing.T, namespace, name string) {
		if strings.Contains(namespace, "/") || strings.Contains(name, "/") {
			return
		}

		key := kclient.ObjectKey{Namespace: namespace, Name: name}
		identifier := UnparseExporterIdentifier(key)

		parsed, err := ParseExporterIdentifier(identifier)
		if err != nil {
			t.Fatalf("ParseExporterIdentifier(UnparseExporterIdentifier(%v)) failed: %v", key, err)
		}

		if *parsed != key {
			t.Errorf("round-trip failed: input=%v, unparsed=%q, parsed=%v", key, identifier, *parsed)
		}
	})
}

func FuzzParseUnparseLeaseIdentifier(f *testing.F) {
	f.Add("default", "lease-001")
	f.Add("jumpstarter-lab", "test-lease")
	f.Add("ns", "name")
	f.Add("", "")

	f.Fuzz(func(t *testing.T, namespace, name string) {
		if strings.Contains(namespace, "/") || strings.Contains(name, "/") {
			return
		}

		key := kclient.ObjectKey{Namespace: namespace, Name: name}
		identifier := UnparseLeaseIdentifier(key)

		parsed, err := ParseLeaseIdentifier(identifier)
		if err != nil {
			t.Fatalf("ParseLeaseIdentifier(UnparseLeaseIdentifier(%v)) failed: %v", key, err)
		}

		if *parsed != key {
			t.Errorf("round-trip failed: input=%v, unparsed=%q, parsed=%v", key, identifier, *parsed)
		}
	})
}

func FuzzParseUnparseObjectIdentifier(f *testing.F) {
	f.Add("default", "my-obj", "exporters")
	f.Add("ns", "name", "leases")
	f.Add("ns", "name", "clients")
	f.Add("", "", "exporters")

	f.Fuzz(func(t *testing.T, namespace, name, kind string) {
		if strings.Contains(namespace, "/") || strings.Contains(name, "/") || strings.Contains(kind, "/") {
			return
		}

		key := kclient.ObjectKey{Namespace: namespace, Name: name}
		identifier := UnparseObjectIdentifier(key, kind)

		parsed, err := ParseObjectIdentifier(identifier, kind)
		if err != nil {
			t.Fatalf("ParseObjectIdentifier(UnparseObjectIdentifier(%v, %q), %q) failed: %v", key, kind, kind, err)
		}

		if *parsed != key {
			t.Errorf("round-trip failed: input=%v kind=%q, unparsed=%q, parsed=%v", key, kind, identifier, *parsed)
		}
	})
}

func FuzzParseNamespaceIdentifier(f *testing.F) {
	f.Add("namespaces/default")
	f.Add("namespaces/jumpstarter-lab")
	f.Add("namespaces/")
	f.Add("")
	f.Add("invalid")
	f.Add("namespaces/ns/extra")
	f.Add("wrong/ns")

	f.Fuzz(func(t *testing.T, identifier string) {
		namespace, err := ParseNamespaceIdentifier(identifier)
		if err != nil {
			return
		}

		reconstructed := "namespaces/" + namespace
		namespace2, err := ParseNamespaceIdentifier(reconstructed)
		if err != nil {
			t.Errorf("round-trip failed: ParseNamespaceIdentifier(%q) = %q, but re-parse of %q failed: %v", identifier, namespace, reconstructed, err)
			return
		}
		if namespace != namespace2 {
			t.Errorf("round-trip not stable: %q vs %q", namespace, namespace2)
		}
	})
}

func FuzzParseExporterIdentifierRobust(f *testing.F) {
	f.Add("namespaces/default/exporters/my-exporter")
	f.Add("")
	f.Add("invalid")
	f.Add("namespaces/ns/leases/wrong-kind")
	f.Add("namespaces/ns")
	f.Add("namespaces/ns/exporters/name/extra")

	f.Fuzz(func(t *testing.T, identifier string) {
		_, _ = ParseExporterIdentifier(identifier)
	})
}

func FuzzParseLeaseIdentifierRobust(f *testing.F) {
	f.Add("namespaces/default/leases/lease-001")
	f.Add("")
	f.Add("invalid")
	f.Add("namespaces/ns/exporters/wrong-kind")

	f.Fuzz(func(t *testing.T, identifier string) {
		_, _ = ParseLeaseIdentifier(identifier)
	})
}

func FuzzParseClientIdentifier(f *testing.F) {
	f.Add("namespaces/default/clients/my-client")
	f.Add("")
	f.Add("invalid")
	f.Add("namespaces/ns/exporters/wrong-kind")

	f.Fuzz(func(t *testing.T, identifier string) {
		key, err := ParseClientIdentifier(identifier)
		if err != nil {
			return
		}

		reconstructed := UnparseObjectIdentifier(*key, "clients")
		key2, err := ParseClientIdentifier(reconstructed)
		if err != nil {
			t.Errorf("round-trip failed: parsed %q to %v, unparsed to %q, but re-parse failed: %v", identifier, *key, reconstructed, err)
			return
		}
		if *key != *key2 {
			t.Errorf("round-trip not stable: %v vs %v", *key, *key2)
		}
	})
}
