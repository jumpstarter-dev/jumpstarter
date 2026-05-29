package service

import (
	"testing"
)

func FuzzMatchLabels(f *testing.F) {
	f.Add("app", "myapp", "app", "myapp")
	f.Add("app", "myapp", "app", "other")
	f.Add("", "", "", "")
	f.Add("key1", "val1", "key2", "val2")

	f.Fuzz(func(t *testing.T, ck, cv, tk, tv string) {
		candidate := map[string]string{ck: cv}
		target := map[string]string{tk: tv}
		// MatchLabels must not panic.
		result := MatchLabels(candidate, target)

		if ck == tk && cv == tv {
			if result != 1 {
				t.Errorf("MatchLabels with matching key/value returned %d, expected 1", result)
			}
		}
	})
}
