package v1alpha1

import "strings"

func (e *Exporter) InternalSubject() string {
	return strings.Join([]string{"exporter", e.Namespace, e.Name, string(e.UID)}, ":")
}

func (e *Exporter) Usernames(prefix string) []string {
	usernames := []string{prefix + e.InternalSubject()}

	if e.Spec.Username != nil {
		usernames = append(usernames, *e.Spec.Username)
	}

	return usernames
}
