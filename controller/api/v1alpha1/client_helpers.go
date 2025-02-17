package v1alpha1

import "strings"

func (c *Client) Username(prefix string) string {
	if c.Spec.Username != nil {
		return *c.Spec.Username
	} else {
		return prefix + strings.Join([]string{"client", c.Namespace, c.Name, string(c.UID)}, ":")
	}
}
