package v1alpha1

import (
	"strings"
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

func FuzzExporterUsernames(f *testing.F) {
	f.Add("internal:", "my-exporter", "default", "uid-123", "", false)
	f.Add("internal:", "exporter", "ns", "uid", "custom-user", true)
	f.Add("prefix:", "name", "namespace", "uid", "", false)
	f.Add("", "name", "ns", "uid", "user", true)

	f.Fuzz(func(t *testing.T, prefix, name, namespace, uid, customUsername string, hasCustom bool) {
		e := &Exporter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: namespace,
				UID:       types.UID(uid),
			},
		}
		if hasCustom {
			e.Spec.Username = &customUsername
		}

		usernames := e.Usernames(prefix)

		if len(usernames) == 0 {
			t.Fatal("Usernames returned empty slice")
		}

		expectedInternal := prefix + e.InternalSubject()
		if usernames[0] != expectedInternal {
			t.Errorf("first username = %q, expected %q", usernames[0], expectedInternal)
		}

		if !strings.HasPrefix(usernames[0], prefix) {
			t.Errorf("first username %q does not start with prefix %q", usernames[0], prefix)
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

func FuzzExporterToProtobuf(f *testing.F) {
	f.Add("my-exporter", "default", "Available", "running fine")
	f.Add("exp", "ns", "Offline", "")
	f.Add("exp", "ns", "Unspecified", "msg")
	f.Add("exp", "ns", "LeaseReady", "msg")
	f.Add("exp", "ns", "UnknownStatus", "msg")

	f.Fuzz(func(t *testing.T, name, namespace, statusValue, statusMessage string) {
		e := &Exporter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: namespace,
			},
			Status: ExporterStatus{
				ExporterStatusValue: statusValue,
				StatusMessage:       statusMessage,
			},
		}

		pb := e.ToProtobuf()

		expectedName := "namespaces/" + namespace + "/exporters/" + name
		if pb.Name != expectedName {
			t.Errorf("protobuf Name = %q, expected %q", pb.Name, expectedName)
		}

		if pb.StatusMessage != statusMessage {
			t.Errorf("protobuf StatusMessage = %q, expected %q", pb.StatusMessage, statusMessage)
		}
	})
}

func FuzzExporterListToProtobuf(f *testing.F) {
	f.Add("exp1", "exp2", "ns")

	f.Fuzz(func(t *testing.T, name1, name2, namespace string) {
		list := &ExporterList{
			Items: []Exporter{
				{ObjectMeta: metav1.ObjectMeta{Name: name1, Namespace: namespace}},
				{ObjectMeta: metav1.ObjectMeta{Name: name2, Namespace: namespace}},
			},
		}

		resp := list.ToProtobuf()

		if len(resp.Exporters) != 2 {
			t.Fatalf("expected 2 exporters in protobuf response, got %d", len(resp.Exporters))
		}
	})
}
