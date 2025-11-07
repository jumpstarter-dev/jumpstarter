package v1alpha1

import "strings"

func (c *Client) InternalSubject() string {
	namespace, uid := getNamespaceAndUID(c.Namespace, c.UID, c.Annotations)
	return strings.Join([]string{"client", namespace, c.Name, uid}, ":")
}

func (c *Client) Usernames(prefix string) []string {
	usernames := []string{prefix + c.InternalSubject()}

	if c.Spec.Username != nil {
		usernames = append(usernames, *c.Spec.Username)
	}

	return usernames
}
