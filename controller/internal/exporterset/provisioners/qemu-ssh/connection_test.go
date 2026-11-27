/*
Copyright 2026 The Jumpstarter Authors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package qemussh

import (
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func exporterWithAnnotations(ann map[string]string) *jumpstarterdevv1alpha1.Exporter {
	return &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "test-exp", Annotations: ann},
	}
}

func TestConnectionFromExporter_recorded(t *testing.T) {
	exp := exporterWithAnnotations(map[string]string{
		AnnotationConnection: `{"name":"lab-1","port":2222,"user":"deploy","secret":"ssh-key"}`,
	})
	got, present, err := connectionFromExporter(exp)
	if err != nil {
		t.Fatalf("connectionFromExporter() err = %v", err)
	}
	if !present {
		t.Fatal("present should be true for a recorded connection")
	}
	want := ConnectionConfig{
		HostConfig: HostConfig{Host: "lab-1", Port: 2222, User: "deploy"},
		Secret:     "ssh-key",
	}
	if got != want {
		t.Errorf("connection = %+v, want %+v", got, want)
	}
}

func TestConnectionFromExporter_absent(t *testing.T) {
	for _, ann := range []map[string]string{nil, {}} {
		_, present, err := connectionFromExporter(exporterWithAnnotations(ann))
		if present || err != nil {
			t.Errorf("connectionFromExporter(%v) = present=%v err=%v, want false, nil", ann, present, err)
		}
	}
}

// A malformed or incomplete connection annotation is distinguished from an
// absent one: present is true (resources may exist) and err is non-nil, so the
// caller can warn about orphans rather than silently skipping.
func TestConnectionFromExporter_corrupt(t *testing.T) {
	for _, raw := range []string{
		"{not-json",
		`{"name":"","port":22,"user":"root","secret":"s"}`,
		`{"name":"h","port":0,"user":"root","secret":"s"}`,
		`{"name":"h","port":22,"user":"","secret":"s"}`,
		`{"name":"h","port":22,"user":"root","secret":""}`,
	} {
		_, present, err := connectionFromExporter(exporterWithAnnotations(map[string]string{AnnotationConnection: raw}))
		if !present {
			t.Errorf("connectionFromExporter(%q) present = false, want true (annotation exists)", raw)
		}
		if err == nil {
			t.Errorf("connectionFromExporter(%q) err = nil, want an error for a corrupt/incomplete record", raw)
		}
	}
}
