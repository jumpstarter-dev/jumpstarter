package v1alpha1

type Device struct {
	Uuid       string            `json:"uuid,omitempty"`
	ParentUuid *string           `json:"parent_uuid,omitempty"`
	Labels     map[string]string `json:"labels,omitempty"`
}
