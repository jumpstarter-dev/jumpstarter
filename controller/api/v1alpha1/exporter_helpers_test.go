package v1alpha1

import (
	"strings"
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

func TestExporter_InternalSubject(t *testing.T) {
	t.Run("without annotations", func(t *testing.T) {
		e := &Exporter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "my-exporter",
				Namespace: "default",
				UID:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
			},
		}
		expected := "exporter:default:my-exporter:123e4567-e89b-12d3-a456-426614174000"
		if got := e.InternalSubject(); got != expected {
			t.Errorf("got %v, want %v", got, expected)
		}
	})

	t.Run("with both migrated annotations", func(t *testing.T) {
		e := &Exporter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "my-exporter",
				Namespace: "default",
				UID:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
				Annotations: map[string]string{
					AnnotationMigratedNamespace: "old-namespace",
					AnnotationMigratedUID:       "old-uid-value",
				},
			},
		}
		expected := "exporter:old-namespace:my-exporter:old-uid-value"
		if got := e.InternalSubject(); got != expected {
			t.Errorf("got %v, want %v", got, expected)
		}
	})

	t.Run("empty annotation values are ignored", func(t *testing.T) {
		e := &Exporter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "my-exporter",
				Namespace: "default",
				UID:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
				Annotations: map[string]string{
					AnnotationMigratedNamespace: "",
					AnnotationMigratedUID:       "",
				},
			},
		}
		expected := "exporter:default:my-exporter:123e4567-e89b-12d3-a456-426614174000"
		if got := e.InternalSubject(); got != expected {
			t.Errorf("got %v, want %v", got, expected)
		}
	})
}

// TestValidateExporterEnabledForLease verifies the shared disabled-exporter rule.
func TestValidateExporterEnabledForLease(t *testing.T) {
	disabled := false

	tests := []struct {
		name          string
		exporter      *Exporter
		allowDisabled bool
		wantError     bool
	}{
		{
			name:      "nil exporter",
			exporter:  nil,
			wantError: false,
		},
		{
			name: "enabled exporter",
			exporter: &Exporter{
				ObjectMeta: metav1.ObjectMeta{Name: "enabled"},
			},
			wantError: false,
		},
		{
			name: "disabled exporter without override",
			exporter: &Exporter{
				ObjectMeta: metav1.ObjectMeta{Name: "disabled"},
				Spec:       ExporterSpec{Enabled: &disabled},
			},
			wantError: true,
		},
		{
			name: "disabled exporter with override",
			exporter: &Exporter{
				ObjectMeta: metav1.ObjectMeta{Name: "disabled"},
				Spec:       ExporterSpec{Enabled: &disabled},
			},
			allowDisabled: true,
			wantError:     false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateExporterEnabledForLease(tt.exporter, tt.allowDisabled)
			if (err != nil) != tt.wantError {
				t.Fatalf("ValidateExporterEnabledForLease() error = %v, wantError %v", err, tt.wantError)
			}
			if tt.wantError && !strings.Contains(err.Error(), "requested exporter disabled is disabled") {
				t.Fatalf("unexpected error: %v", err)
			}
		})
	}
}

func TestExporter_Usernames(t *testing.T) {
	t.Run("without custom username", func(t *testing.T) {
		e := &Exporter{
			ObjectMeta: metav1.ObjectMeta{Name: "my-exporter", Namespace: "default", UID: types.UID("123")},
			Spec:       ExporterSpec{},
		}
		got := e.Usernames("internal:")
		if len(got) != 1 || got[0] != "internal:exporter:default:my-exporter:123" {
			t.Errorf("got %v, want single internal subject", got)
		}
	})

	t.Run("with custom username", func(t *testing.T) {
		e := &Exporter{
			ObjectMeta: metav1.ObjectMeta{Name: "my-exporter", Namespace: "default", UID: types.UID("123")},
			Spec:       ExporterSpec{Username: new("custom-user")},
		}
		got := e.Usernames("internal:")
		if len(got) != 2 || got[1] != "custom-user" {
			t.Errorf("got %v, want internal subject and custom username", got)
		}
	})
}
