package v1alpha1

import (
	"strings"
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

func FuzzClientUsernames(f *testing.F) {
	f.Add("internal:", "my-client", "default", "uid-123", "", false)
	f.Add("internal:", "client", "ns", "uid", "oidc:user@example.com", true)
	f.Add("prefix:", "name", "namespace", "uid", "", false)
	f.Add("", "name", "ns", "uid", "user", true)

	f.Fuzz(func(t *testing.T, prefix, name, namespace, uid, customUsername string, hasCustom bool) {
		c := &Client{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: namespace,
				UID:       types.UID(uid),
			},
		}
		if hasCustom {
			c.Spec.Username = &customUsername
		}

		usernames := c.Usernames(prefix)

		if len(usernames) == 0 {
			t.Fatal("Usernames returned empty slice")
		}

		expectedInternal := prefix + c.InternalSubject()
		if usernames[0] != expectedInternal {
			t.Errorf("first username = %q, expected %q", usernames[0], expectedInternal)
		}

		if !strings.HasPrefix(usernames[0], prefix) {
			t.Errorf("first username %q does not start with prefix %q", usernames[0], prefix)
		}

		internalSubject := c.InternalSubject()
		if !strings.HasPrefix(internalSubject, "client:") {
			t.Errorf("InternalSubject() = %q does not start with 'client:'", internalSubject)
		}

		if hasCustom {
			if len(usernames) != 2 {
				t.Errorf("expected 2 usernames with custom username, got %d", len(usernames))
			} else if usernames[1] != customUsername {
				t.Errorf("second username = %q, expected custom %q", usernames[1], customUsername)
			}
		} else {
			if len(usernames) != 1 {
				t.Errorf("expected 1 username without custom username, got %d", len(usernames))
			}
		}
	})
}
