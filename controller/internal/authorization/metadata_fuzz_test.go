package authorization

import (
	"regexp"
	"strings"
	"testing"
)

var dnsLabelPattern = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`)

func FuzzNormalizeOIDCUsername(f *testing.F) {
	f.Add("dex:test-exporter-hooks")
	f.Add("internal:admin")
	f.Add("dex:foo@example.com")
	f.Add("foo")
	f.Add("foo@example.com")
	f.Add("dex:system:serviceaccount:jumpstarter-lab:test-exporter-sa")
	f.Add("dex:system:serviceaccount:default:my-sa")
	f.Add("")
	f.Add("prefix:with:multiple:colons")
	f.Add("@foo@example.com@")
	f.Add("foo@@@@@example.com")

	f.Fuzz(func(t *testing.T, username string) {
		result := normalizeOIDCUsername(username)

		if len(result) > 63 {
			t.Errorf("normalizeOIDCUsername(%q) produced result longer than 63 chars: %d", username, len(result))
		}

		if result == "" {
			return
		}

		if !dnsLabelPattern.MatchString(result) {
			t.Errorf("normalizeOIDCUsername(%q) = %q does not match DNS label pattern", username, result)
		}

		if strings.Contains(result, "--") {
			t.Errorf("normalizeOIDCUsername(%q) = %q contains consecutive hyphens", username, result)
		}

		if result[0] == '-' || result[len(result)-1] == '-' {
			t.Errorf("normalizeOIDCUsername(%q) = %q has leading or trailing hyphen", username, result)
		}

		second := normalizeOIDCUsername(username)
		if result != second {
			t.Errorf("normalizeOIDCUsername(%q) is not deterministic: %q vs %q", username, result, second)
		}
	})
}

func FuzzStripOIDCPrefix(f *testing.F) {
	f.Add("dex:test-user")
	f.Add("internal:admin")
	f.Add("test-user")
	f.Add("")
	f.Add("prefix:with:multiple:colons")
	f.Add("dex:system:serviceaccount:jumpstarter-lab:test-exporter-sa")

	f.Fuzz(func(t *testing.T, username string) {
		result := stripOIDCPrefix(username)

		if !strings.Contains(username, ":") {
			if result != username {
				t.Errorf("stripOIDCPrefix(%q) = %q, expected unchanged input (no colon)", username, result)
			}
		}

		if isKubernetesServiceAccount(username) {
			parts := strings.Split(username, ":")
			expected := parts[3] + ":" + parts[4]
			if result != expected {
				t.Errorf("stripOIDCPrefix(%q) = %q, expected service account format %q", username, result, expected)
			}
		}
	})
}

func FuzzNormalizeName(f *testing.F) {
	f.Add("foo")
	f.Add("foo@example.com")
	f.Add("foo@@@@@example.com")
	f.Add("@foo@example.com@")
	f.Add("")

	f.Fuzz(func(t *testing.T, name string) {
		result := normalizeName(name)

		if !strings.HasPrefix(result, "oidc-") {
			t.Errorf("normalizeName(%q) = %q does not start with 'oidc-'", name, result)
		}

		parts := strings.Split(result, "-")
		hexSuffix := parts[len(parts)-1]
		if len(hexSuffix) != 6 {
			t.Errorf("normalizeName(%q) = %q does not end with a 6-char hex hash (got %q)", name, result, hexSuffix)
		}
		for _, c := range hexSuffix {
			if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
				t.Errorf("normalizeName(%q) = %q has non-hex char %c in suffix", name, result, c)
			}
		}

		second := normalizeName(name)
		if result != second {
			t.Errorf("normalizeName(%q) is not deterministic: %q vs %q", name, result, second)
		}
	})
}
