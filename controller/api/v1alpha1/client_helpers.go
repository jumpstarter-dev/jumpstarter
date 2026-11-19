package v1alpha1

import "strings"

func (c *Client) InternalSubject() string {
	namespace, uid := getNamespaceAndUID(c.Namespace, c.UID, c.Annotations)
	return strings.Join([]string{"client", namespace, c.Name, uid}, ":")
}

func (c *Client) CredentialSecretName() string {
	return c.Name + "-client"
}
