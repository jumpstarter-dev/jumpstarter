package v1alpha1

type Device struct {
	Uuid            string            `json:"uuid,omitempty"`
	DriverInterface string            `json:"driver_interface,omitempty"`
	Labels          map[string]string `json:"labels,omitempty"`
}
