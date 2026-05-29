package authorization

import (
	"testing"
)

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
		// normalizeOIDCUsername must not panic on any input.
		result := normalizeOIDCUsername(username)

		// The result must be a valid DNS label (max 63 chars, no leading/trailing hyphens)
		if len(result) > 63 {
			t.Errorf("normalizeOIDCUsername(%q) produced result longer than 63 chars: %d", username, len(result))
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
		// stripOIDCPrefix must not panic.
		_ = stripOIDCPrefix(username)
	})
}

func FuzzNormalizeName(f *testing.F) {
	f.Add("foo")
	f.Add("foo@example.com")
	f.Add("foo@@@@@example.com")
	f.Add("@foo@example.com@")
	f.Add("")

	f.Fuzz(func(t *testing.T, name string) {
		// normalizeName must not panic.
		_ = normalizeName(name)
	})
}
