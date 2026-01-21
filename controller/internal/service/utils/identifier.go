package utils

import (
	"fmt"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

func ParseNamespaceIdentifier(identifier string) (namespace string, err error) {
	segments := strings.Split(identifier, "/")

	if len(segments) != 2 {
		return "", status.Errorf(
			codes.InvalidArgument,
			"invalid number of segments in identifier \"%s\", expecting 2, got %d",
			identifier,
			len(segments),
		)
	}

	if segments[0] != "namespaces" {
		return "", status.Errorf(
			codes.InvalidArgument,
			"invalid first segment in identifier \"%s\", expecting \"namespaces\", got \"%s\"",
			identifier,
			segments[0],
		)
	}

	return segments[1], nil
}

func ParseObjectIdentifier(identifier string, kind string) (key *kclient.ObjectKey, err error) {
	segments := strings.Split(identifier, "/")

	if len(segments) != 4 {
		return nil, status.Errorf(
			codes.InvalidArgument,
			"invalid number of segments in identifier \"%s\", expecting 4, got %d",
			identifier,
			len(segments),
		)
	}

	if segments[0] != "namespaces" {
		return nil, status.Errorf(
			codes.InvalidArgument,
			"invalid first segment in identifier \"%s\", expecting \"namespaces\", got \"%s\"",
			identifier,
			segments[0],
		)
	}

	if segments[2] != kind {
		return nil, status.Errorf(
			codes.InvalidArgument,
			"invalid third segment in identifier \"%s\", expecting \"%s\", got \"%s\"",
			identifier,
			kind,
			segments[2],
		)
	}

	return &kclient.ObjectKey{
		Namespace: segments[1],
		Name:      segments[3],
	}, nil
}

func UnparseObjectIdentifier(key kclient.ObjectKey, kind string) string {
	return fmt.Sprintf("namespaces/%s/%s/%s", key.Namespace, kind, key.Name)
}

func ParseExporterIdentifier(identifier string) (key *kclient.ObjectKey, err error) {
	return ParseObjectIdentifier(identifier, "exporters")
}

func UnparseExporterIdentifier(key kclient.ObjectKey) string {
	return UnparseObjectIdentifier(key, "exporters")
}

func ParseLeaseIdentifier(identifier string) (key *kclient.ObjectKey, err error) {
	return ParseObjectIdentifier(identifier, "leases")
}

func UnparseLeaseIdentifier(key kclient.ObjectKey) string {
	return UnparseObjectIdentifier(key, "leases")
}

func ParseClientIdentifier(identifier string) (key *kclient.ObjectKey, err error) {
	return ParseObjectIdentifier(identifier, "clients")
}
