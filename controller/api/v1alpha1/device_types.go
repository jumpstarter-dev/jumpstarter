package v1alpha1

// Device represents a driver instance reported by an exporter.
type Device struct {
	// Uuid is the unique identifier of the device within the exporter.
	Uuid string `json:"uuid,omitempty"`
	// ParentUuid is the UUID of the parent device, if this is a child device.
	ParentUuid *string `json:"parent_uuid,omitempty"`
	// Labels are key-value pairs associated with the device.
	Labels map[string]string `json:"labels,omitempty"`
}
