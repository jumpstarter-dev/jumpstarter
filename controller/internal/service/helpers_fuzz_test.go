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
		result := MatchLabels(candidate, target)

		if ck == tk && cv == tv {
			if result != 1 {
				t.Errorf("MatchLabels with matching key/value returned %d, expected 1", result)
			}
		}

		if ck != tk {
			if result != -1 {
				t.Errorf("MatchLabels with disjoint keys (%q vs %q) returned %d, expected -1", ck, tk, result)
			}
		}

		if ck == tk && cv != tv {
			if result != -1 {
				t.Errorf("MatchLabels with same key %q but different values (%q vs %q) returned %d, expected -1", ck, cv, tv, result)
			}
		}

		selfResult := MatchLabels(candidate, candidate)
		if selfResult != len(candidate) {
			t.Errorf("MatchLabels(self, self) returned %d, expected %d", selfResult, len(candidate))
		}

		emptyResult := MatchLabels(candidate, map[string]string{})
		if emptyResult != 0 {
			t.Errorf("MatchLabels(candidate, empty) returned %d, expected 0", emptyResult)
		}
	})
}
