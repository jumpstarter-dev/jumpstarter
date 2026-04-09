package authorization

import (
	"context"
	"strings"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"k8s.io/apimachinery/pkg/util/validation"
	"k8s.io/apiserver/pkg/authentication/user"
)

func TestStripOIDCPrefix(t *testing.T) {
	testcases := []struct {
		input  string
		output string
	}{
		{
			input:  "dex:test-user",
			output: "test-user",
		},
		{
			input:  "internal:admin",
			output: "admin",
		},
		{
			input:  "test-user",
			output: "test-user",
		},
		{
			input:  "prefix:with:multiple:colons",
			output: "with:multiple:colons",
		},
		{
			input:  "",
			output: "",
		},
		{
			input:  "dex:system:serviceaccount:jumpstarter-lab:test-exporter-sa",
			output: "jumpstarter-lab:test-exporter-sa",
		},
		{
			input:  "dex:system:serviceaccount:default:my-sa",
			output: "default:my-sa",
		},
	}
	for _, testcase := range testcases {
		result := stripOIDCPrefix(testcase.input)
		if result != testcase.output {
			t.Errorf("stripOIDCPrefix(%q) = %q, expected %q",
				testcase.input, result, testcase.output)
		}
	}
}

func TestNormalizeName(t *testing.T) {
	testcases := []struct {
		input  string
		output string
	}{
		{
			input:  "foo",
			output: "oidc-foo-2c26b4",
		},
		{
			input:  "foo@example.com",
			output: "oidc-foo-example-com-321ba1",
		},
		{
			input:  "foo@@@@@example.com",
			output: "oidc-foo-example-com-5ac340",
		},
		{
			input:  "@foo@example.com@",
			output: "oidc-foo-example-com-5be6ea",
		},
		{
			input:  strings.Repeat("foo", 30),
			output: "oidc-foofoofoofoofoofoofoofoofoofoofoofoof-4ac4a7",
		},
	}
	for _, testcase := range testcases {
		result := normalizeName(testcase.input)
		if validation.IsDNS1123Subdomain(result) != nil {
			t.Errorf("normalizing the name %s does not produce a valid RFC1123 subdomain, but %s",
				testcase.input, result)
		}
		if result != testcase.output {
			t.Errorf("normalizing the name %s does not produce the expected output %s, but %s",
				testcase.input, testcase.output, result)
		}
	}
}

func TestNormalizeOIDCUsername(t *testing.T) {
	testcases := []struct {
		input  string
		output string
	}{
		{
			input:  "dex:test-exporter-hooks",
			output: "test-exporter-hooks",
		},
		{
			input:  "internal:admin",
			output: "admin",
		},
		{
			input:  "dex:foo@example.com",
			output: "foo-example-com",
		},
		{
			input:  "foo",
			output: "foo",
		},
		{
			input:  "foo@example.com",
			output: "foo-example-com",
		},
		{
			input:  strings.Repeat("a", 70),
			output: strings.Repeat("a", 63),
		},
		{
			input:  "dex:system:serviceaccount:jumpstarter-lab:test-exporter-sa",
			output: "jumpstarter-lab-test-exporter-sa",
		},
	}
	for _, testcase := range testcases {
		result := normalizeOIDCUsername(testcase.input)
		if validation.IsDNS1123Subdomain(result) != nil {
			t.Errorf("normalizing the OIDC username %s does not produce a valid RFC1123 subdomain, but %s",
				testcase.input, result)
		}
		if result != testcase.output {
			t.Errorf("normalizing the OIDC username %s does not produce the expected output %s, but %s",
				testcase.input, testcase.output, result)
		}
	}
}

func TestContextAttributes_ExternalOIDC(t *testing.T) {
	getter := NewMetadataAttributesGetter(MetadataAttributesGetterConfig{
		NamespaceKey: "test-namespace",
		ResourceKey:  "test-resource",
		NameKey:      "test-name",
	})

	t.Run("auto-provisioning", func(t *testing.T) {
		// Use a realistic OIDC username with provider prefix
		testUsername := "dex:test-user@example.com"
		// Expected name is hardcoded to avoid circular dependency on the function being tested
		expectedName := "test-user-example-com"
		userInfo := &user.DefaultInfo{Name: testUsername}

		testcases := []struct {
			name          string
			providedName  string
			expectError   bool
			errorContains string
			errorCode     codes.Code
		}{
			{
				name:         "empty name - should use OIDC-derived",
				providedName: "",
				expectError:  false,
			},
			{
				name:         "matching name - should accept",
				providedName: expectedName,
				expectError:  false,
			},
			{
				name:          "mismatched name - should reject",
				providedName:  "arbitrary-name",
				expectError:   true,
				errorContains: "resource name mismatch",
				errorCode:     codes.InvalidArgument,
			},
			{
				name:          "partial match - should reject",
				providedName:  "oidc-test-user",
				expectError:   true,
				errorContains: "resource name mismatch",
				errorCode:     codes.InvalidArgument,
			},
		}

		for _, tc := range testcases {
			t.Run(tc.name, func(t *testing.T) {
				// Create context with metadata
				md := metadata.Pairs(
					"test-namespace", "default",
					"test-resource", "Client",
				)
				if tc.providedName != "" {
					md.Append("test-name", tc.providedName)
				}
				ctx := metadata.NewIncomingContext(context.Background(), md)

				// Call ContextAttributes
				attrs, err := getter.ContextAttributes(ctx, userInfo)

				if tc.expectError {
					if err == nil {
						t.Errorf("expected error but got none")
						return
					}
					st, ok := status.FromError(err)
					if !ok {
						t.Errorf("expected gRPC status error but got: %v", err)
						return
					}
					if st.Code() != tc.errorCode {
						t.Errorf("expected error code %v but got %v", tc.errorCode, st.Code())
					}
					if !strings.Contains(st.Message(), tc.errorContains) {
						t.Errorf("expected error to contain %q but got: %s", tc.errorContains, st.Message())
					}
					// Verify error message includes both provided and expected names
					if tc.providedName != "" && !strings.Contains(st.Message(), tc.providedName) {
						t.Errorf("error message should include provided name %q: %s", tc.providedName, st.Message())
					}
					if !strings.Contains(st.Message(), expectedName) {
						t.Errorf("error message should include expected name %q: %s", expectedName, st.Message())
					}
				} else {
					if err != nil {
						t.Errorf("expected no error but got: %v", err)
						return
					}
					if attrs == nil {
						t.Errorf("expected attrs but got nil")
						return
					}
					if attrs.GetName() != expectedName {
						t.Errorf("expected name %q but got %q", expectedName, attrs.GetName())
					}
					if attrs.GetUser().GetName() != testUsername {
						t.Errorf("expected username %q but got %q", testUsername, attrs.GetUser().GetName())
					}
				}
			})
		}
	})
}

func TestContextAttributes_InternalOIDC(t *testing.T) {
	getter := NewMetadataAttributesGetter(MetadataAttributesGetterConfig{
		NamespaceKey: "test-namespace",
		ResourceKey:  "test-resource",
		NameKey:      "test-name",
	})

	t.Run("no auto-provisioning", func(t *testing.T) {
		testcases := []struct {
			name          string
			username      string
			providedName  string
			expectError   bool
			errorContains string
			errorCode     codes.Code
		}{
			{
				name:          "internal with no name - should error",
				username:      "internal:exporter:default:my-exporter:uuid",
				providedName:  "",
				expectError:   true,
				errorContains: "resource name required",
				errorCode:     codes.InvalidArgument,
			},
			{
				name:         "internal with name - should accept as-is",
				username:     "internal:exporter:default:my-exporter:uuid",
				providedName: "my-exporter",
				expectError:  false,
			},
			{
				name:         "internal with different name - should accept",
				username:     "internal:client:default:test:uuid",
				providedName: "arbitrary-name",
				expectError:  false,
			},
		}

		for _, tc := range testcases {
			t.Run(tc.name, func(t *testing.T) {
				userInfo := &user.DefaultInfo{Name: tc.username}

				// Create context with metadata
				md := metadata.Pairs(
					"test-namespace", "default",
					"test-resource", "Client",
				)
				if tc.providedName != "" {
					md.Append("test-name", tc.providedName)
				}
				ctx := metadata.NewIncomingContext(context.Background(), md)

				// Call ContextAttributes
				attrs, err := getter.ContextAttributes(ctx, userInfo)

				if tc.expectError {
					if err == nil {
						t.Errorf("expected error but got none")
						return
					}
					st, ok := status.FromError(err)
					if !ok {
						t.Errorf("expected gRPC status error but got: %v", err)
						return
					}
					if st.Code() != tc.errorCode {
						t.Errorf("expected error code %v but got %v", tc.errorCode, st.Code())
					}
					if !strings.Contains(st.Message(), tc.errorContains) {
						t.Errorf("expected error to contain %q but got: %s", tc.errorContains, st.Message())
					}
				} else {
					if err != nil {
						t.Errorf("expected no error but got: %v", err)
						return
					}
					if attrs == nil {
						t.Errorf("expected attrs but got nil")
						return
					}
					if attrs.GetName() != tc.providedName {
						t.Errorf("expected name %q but got %q", tc.providedName, attrs.GetName())
					}
					if attrs.GetUser().GetName() != tc.username {
						t.Errorf("expected username %q but got %q", tc.username, attrs.GetUser().GetName())
					}
				}
			})
		}
	})
}

func TestContextAttributes_KubernetesServiceAccount(t *testing.T) {
	getter := NewMetadataAttributesGetter(MetadataAttributesGetterConfig{
		NamespaceKey: "test-namespace",
		ResourceKey:  "test-resource",
		NameKey:      "test-name",
	})

	t.Run("no auto-provisioning", func(t *testing.T) {
		testcases := []struct {
			name          string
			username      string
			providedName  string
			expectError   bool
			errorContains string
			errorCode     codes.Code
		}{
			{
				name:          "k8s sa with no name - should error",
				username:      "dex:system:serviceaccount:jumpstarter-lab:test-exporter-sa",
				providedName:  "",
				expectError:   true,
				errorContains: "resource name required",
				errorCode:     codes.InvalidArgument,
			},
			{
				name:         "k8s sa with matching sa name - should accept",
				username:     "dex:system:serviceaccount:jumpstarter-lab:test-exporter-sa",
				providedName: "test-exporter-sa",
				expectError:  false,
			},
			{
				name:         "k8s sa with arbitrary name - should accept",
				username:     "dex:system:serviceaccount:default:my-sa",
				providedName: "any-name-works",
				expectError:  false,
			},
		}

		for _, tc := range testcases {
			t.Run(tc.name, func(t *testing.T) {
				userInfo := &user.DefaultInfo{Name: tc.username}

				// Create context with metadata
				md := metadata.Pairs(
					"test-namespace", "default",
					"test-resource", "Exporter",
				)
				if tc.providedName != "" {
					md.Append("test-name", tc.providedName)
				}
				ctx := metadata.NewIncomingContext(context.Background(), md)

				// Call ContextAttributes
				attrs, err := getter.ContextAttributes(ctx, userInfo)

				if tc.expectError {
					if err == nil {
						t.Errorf("expected error but got none")
						return
					}
					st, ok := status.FromError(err)
					if !ok {
						t.Errorf("expected gRPC status error but got: %v", err)
						return
					}
					if st.Code() != tc.errorCode {
						t.Errorf("expected error code %v but got %v", tc.errorCode, st.Code())
					}
					if !strings.Contains(st.Message(), tc.errorContains) {
						t.Errorf("expected error to contain %q but got: %s", tc.errorContains, st.Message())
					}
				} else {
					if err != nil {
						t.Errorf("expected no error but got: %v", err)
						return
					}
					if attrs == nil {
						t.Errorf("expected attrs but got nil")
						return
					}
					if attrs.GetName() != tc.providedName {
						t.Errorf("expected name %q but got %q", tc.providedName, attrs.GetName())
					}
					if attrs.GetUser().GetName() != tc.username {
						t.Errorf("expected username %q but got %q", tc.username, attrs.GetUser().GetName())
					}
				}
			})
		}
	})
}
