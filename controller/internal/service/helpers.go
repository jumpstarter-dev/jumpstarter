package service

import (
	"fmt"
	"strings"
)

func ParseNamespaceIdentifier(identifier string) (string, error) {
	segments := strings.Split(identifier, "/")
	if len(segments) != 2 {
		return "", fmt.Errorf("incorrect number of segments in identifier, expecting 2, got %d", len(segments))
	}
	if segments[0] != "namespaces" {
		return "", fmt.Errorf("incorrect first segment in identifier, expecting namespaces, got %s", segments[0])
	}
	return segments[1], nil
}

func UnparseExporterIdentifier(namespace string, name string) string {
	return fmt.Sprintf("namespaces/%s/exporters/%s", namespace, name)
}

func ParseExporterIdentifier(identifier string) (string, string, error) {
	segments := strings.Split(identifier, "/")
	if len(segments) != 4 {
		return "", "", fmt.Errorf("incorrect number of segments in identifier, expecting 4, got %d", len(segments))
	}
	if segments[0] != "namespaces" {
		return "", "", fmt.Errorf("incorrect first segment in identifier, expecting namespaces, got %s", segments[0])
	}
	if segments[2] != "exporters" {
		return "", "", fmt.Errorf("incorrect third segment in identifier, expecting exporters, got %s", segments[2])
	}
	return segments[1], segments[3], nil
}
