package v1alpha1

import (
	"testing"

	"k8s.io/apimachinery/pkg/types"
)

func TestGetNamespaceAndUID(t *testing.T) {
	tests := []struct {
		name              string
		namespace         string
		uid               types.UID
		annotations       map[string]string
		expectedNamespace string
		expectedUID       string
	}{
		{
			name:              "no annotations",
			namespace:         "default",
			uid:               types.UID("123e4567-e89b-12d3-a456-426614174000"),
			annotations:       nil,
			expectedNamespace: "default",
			expectedUID:       "123e4567-e89b-12d3-a456-426614174000",
		},
		{
			name:              "empty annotations map",
			namespace:         "default",
			uid:               types.UID("123e4567-e89b-12d3-a456-426614174000"),
			annotations:       map[string]string{},
			expectedNamespace: "default",
			expectedUID:       "123e4567-e89b-12d3-a456-426614174000",
		},
		{
			name:      "migrated namespace only",
			namespace: "default",
			uid:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
			annotations: map[string]string{
				AnnotationMigratedNamespace: "migrated-ns",
			},
			expectedNamespace: "migrated-ns",
			expectedUID:       "123e4567-e89b-12d3-a456-426614174000",
		},
		{
			name:      "migrated uid only",
			namespace: "default",
			uid:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
			annotations: map[string]string{
				AnnotationMigratedUID: "migrated-uid-value",
			},
			expectedNamespace: "default",
			expectedUID:       "migrated-uid-value",
		},
		{
			name:      "both migrated namespace and uid",
			namespace: "default",
			uid:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
			annotations: map[string]string{
				AnnotationMigratedNamespace: "migrated-ns",
				AnnotationMigratedUID:       "migrated-uid-value",
			},
			expectedNamespace: "migrated-ns",
			expectedUID:       "migrated-uid-value",
		},
		{
			name:      "empty migrated namespace value ignored",
			namespace: "default",
			uid:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
			annotations: map[string]string{
				AnnotationMigratedNamespace: "",
			},
			expectedNamespace: "default",
			expectedUID:       "123e4567-e89b-12d3-a456-426614174000",
		},
		{
			name:      "empty migrated uid value ignored",
			namespace: "default",
			uid:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
			annotations: map[string]string{
				AnnotationMigratedUID: "",
			},
			expectedNamespace: "default",
			expectedUID:       "123e4567-e89b-12d3-a456-426614174000",
		},
		{
			name:      "both empty values ignored",
			namespace: "default",
			uid:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
			annotations: map[string]string{
				AnnotationMigratedNamespace: "",
				AnnotationMigratedUID:       "",
			},
			expectedNamespace: "default",
			expectedUID:       "123e4567-e89b-12d3-a456-426614174000",
		},
		{
			name:      "other annotations present",
			namespace: "default",
			uid:       types.UID("123e4567-e89b-12d3-a456-426614174000"),
			annotations: map[string]string{
				"other.annotation/key":      "value",
				AnnotationMigratedNamespace: "migrated-ns",
				"another.annotation":        "another-value",
			},
			expectedNamespace: "migrated-ns",
			expectedUID:       "123e4567-e89b-12d3-a456-426614174000",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotNamespace, gotUID := getNamespaceAndUID(tt.namespace, tt.uid, tt.annotations)

			if gotNamespace != tt.expectedNamespace {
				t.Errorf("getNamespaceAndUID() namespace = %v, want %v", gotNamespace, tt.expectedNamespace)
			}
			if gotUID != tt.expectedUID {
				t.Errorf("getNamespaceAndUID() uid = %v, want %v", gotUID, tt.expectedUID)
			}
		})
	}
}

