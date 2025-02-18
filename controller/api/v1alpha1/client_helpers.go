package v1alpha1

import "strings"

func (c *Client) InternalSubject() string {
	return strings.Join([]string{"client", c.Namespace, c.Name, string(c.UID)}, ":")
}

func (c *Client) Usernames(prefix string) []string {
	usernames := []string{prefix + c.InternalSubject()}

	if c.Spec.Username != nil {
		usernames = append(usernames, *c.Spec.Username)
	}

	return usernames
}
