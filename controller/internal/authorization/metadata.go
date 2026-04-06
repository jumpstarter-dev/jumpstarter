package authorization

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
)

var _ = ContextAttributesGetter(&MetadataAttributesGetter{})

var (
	invalidChar       = regexp.MustCompile("[^-a-zA-Z0-9]")
	multipleHyphen    = regexp.MustCompile("-+")
	surroundingHyphen = regexp.MustCompile("^-|-$")
)

type MetadataAttributesGetterConfig struct {
	NamespaceKey string
	ResourceKey  string
	NameKey      string
}

type MetadataAttributesGetter struct {
	config MetadataAttributesGetterConfig
}

func NewMetadataAttributesGetter(config MetadataAttributesGetterConfig) *MetadataAttributesGetter {
	return &MetadataAttributesGetter{
		config: config,
	}
}

// isKubernetesServiceAccount checks if the username represents a Kubernetes service account.
// Pattern: "provider:system:serviceaccount:namespace:name"
func isKubernetesServiceAccount(username string) bool {
	parts := strings.Split(username, ":")
	return len(parts) >= 5 && parts[1] == "system" && parts[2] == "serviceaccount"
}

// stripOIDCPrefix removes the OIDC provider prefix and extracts the meaningful resource name.
// This is only used for external OIDC providers (internal OIDC accepts names as-is).
// Handles several cases:
//   - "dex:test-user" → "test-user"
//   - "dex:system:serviceaccount:namespace:sa-name" → "namespace:sa-name" (Kubernetes service account)
//   - "test-user" → "test-user" (no prefix)
func stripOIDCPrefix(username string) string {
	parts := strings.Split(username, ":")

	// No colons, return as-is
	if len(parts) == 1 {
		return username
	}

	// For Kubernetes service accounts: "provider:system:serviceaccount:namespace:name"
	// Return "namespace:name" to avoid collisions across namespaces
	if len(parts) >= 5 && parts[1] == "system" && parts[2] == "serviceaccount" {
		return parts[3] + ":" + parts[4]
	}

	// Default: strip only the first part (provider prefix)
	return strings.Join(parts[1:], ":")
}

func normalizeName(name string) string {
	hash := sha256.Sum256([]byte(name))

	sanitized := strings.ToLower(name)
	sanitized = invalidChar.ReplaceAllString(sanitized, "-")
	sanitized = multipleHyphen.ReplaceAllString(sanitized, "-")
	sanitized = surroundingHyphen.ReplaceAllString(sanitized, "")

	if len(sanitized) > 37 {
		sanitized = sanitized[:37]
	}

	return strings.Join([]string{
		"oidc",
		sanitized,
		hex.EncodeToString(hash[:3]),
	}, "-")
}

// normalizeOIDCUsername normalizes an OIDC username into a Kubernetes-compliant resource name.
// It strips the OIDC provider prefix (e.g., "dex:", "internal:") and sanitizes the username.
// NOTE: This accepts the risk of name collisions when multiple OIDC providers use the same
// usernames. This is documented as an acceptable limitation.
func normalizeOIDCUsername(username string) string {
	// Strip OIDC provider prefix to get the base username
	baseName := stripOIDCPrefix(username)

	// Sanitize to meet Kubernetes DNS subdomain requirements
	sanitized := strings.ToLower(baseName)
	sanitized = invalidChar.ReplaceAllString(sanitized, "-")
	sanitized = multipleHyphen.ReplaceAllString(sanitized, "-")
	sanitized = surroundingHyphen.ReplaceAllString(sanitized, "")

	// DNS label max length is 63 characters
	if len(sanitized) > 63 {
		sanitized = sanitized[:63]
		// Ensure we don't end with a hyphen after truncation
		sanitized = surroundingHyphen.ReplaceAllString(sanitized, "")
	}

	return sanitized
}

func (b *MetadataAttributesGetter) ContextAttributes(
	ctx context.Context,
	userInfo user.Info,
) (authorizer.Attributes, error) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "missing metadata")
	}

	namespace, err := mdGet(md, b.config.NamespaceKey)
	if err != nil {
		return nil, err
	}

	resource, err := mdGet(md, b.config.ResourceKey)
	if err != nil {
		return nil, err
	}

	// Check if client provided a name via metadata
	providedName, err := mdGet(md, b.config.NameKey)
	if err != nil && status.Code(err) != codes.InvalidArgument {
		// Return errors other than "missing metadata"
		return nil, err
	}

	// Determine the resource name
	var resourceName string

	// Internal tokens (e.g., "internal:...") and Kubernetes service accounts don't use
	// auto-provisioning - the resource already exists, so accept the provided name.
	if strings.HasPrefix(userInfo.GetName(), "internal:") || isKubernetesServiceAccount(userInfo.GetName()) {
		if providedName == "" {
			return nil, status.Errorf(codes.InvalidArgument, "resource name required for pre-existing authentication")
		}
		resourceName = providedName
	} else {
		// For external OIDC providers with auto-provisioning, derive the name from the username
		// to prevent identity confusion.
		expectedName := normalizeOIDCUsername(userInfo.GetName())

		// If a name was provided and doesn't match the expected OIDC-derived name, reject it
		if providedName != "" && providedName != expectedName {
			return nil, status.Errorf(
				codes.InvalidArgument,
				"resource name mismatch: provided %q but expected %q (derived from OIDC username %q)",
				providedName,
				expectedName,
				userInfo.GetName(),
			)
		}

		resourceName = expectedName
	}

	return authorizer.AttributesRecord{
		User:      userInfo,
		Namespace: namespace,
		Resource:  resource,
		Name:      resourceName,
	}, nil
}

func mdGet(md metadata.MD, k string) (string, error) {
	v := md.Get(k)
	if len(v) < 1 {
		return "", status.Errorf(codes.InvalidArgument, "missing metadata: %s", k)
	}
	if len(v) > 1 {
		return "", status.Errorf(codes.InvalidArgument, "multiple metadata: %s", k)
	}
	return v[0], nil
}
