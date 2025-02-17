package v1alpha1

import "strings"

func (e *Exporter) Username(prefix string) string {
	if e.Spec.Username != nil {
		return *e.Spec.Username
	} else {
		return prefix + strings.Join([]string{"exporter", e.Namespace, e.Name, string(e.UID)}, ":")
	}
}
