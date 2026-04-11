package v1alpha1

type Device struct {
	Uuid                string            `json:"uuid,omitempty"`
	ParentUuid          *string           `json:"parent_uuid,omitempty"`
	Labels              map[string]string `json:"labels,omitempty"`
	FileDescriptorProto []byte            `json:"file_descriptor_proto,omitempty"`
	NativeServices      []string          `json:"native_services,omitempty"`
}
