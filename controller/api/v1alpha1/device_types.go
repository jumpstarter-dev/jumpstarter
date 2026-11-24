package v1alpha1

// Device represents a driver instance reported by an exporter.
type Device struct {
	// Uuid is the unique identifier of the device within the exporter.
	Uuid string `json:"uuid,omitempty"`
	// ParentUuid is the UUID of the parent device, if this is a child device.
	ParentUuid *string `json:"parent_uuid,omitempty"`
	// Labels are key-value pairs associated with the device.
	Labels map[string]string `json:"labels,omitempty"`
	// Ports are the ports that the device declares. A forward of a lease can
	// connect them to ports of other exporters.
	// +listType=atomic
	// +kubebuilder:validation:MaxItems=64
	Ports []DevicePort `json:"ports,omitempty"`
}

// PortDirection is the direction of a port. It shows the end that has the
// service, not the direction of the data.
// +kubebuilder:validation:Enum=provides;requires;connects
type PortDirection string

const (
	// PortDirectionProvides: a local service listens.
	PortDirectionProvides PortDirection = "provides"
	// PortDirectionRequires: a local driver uses a service on a different
	// member through a listener of the exporter.
	PortDirectionRequires PortDirection = "requires"
	// PortDirectionConnects: the port connects to a port of its own type.
	PortDirectionConnects PortDirection = "connects"
)

// PortAttachment says how a port is attached to its device.
// +kubebuilder:validation:Enum=socket;wired;wireless
type PortAttachment string

const (
	// PortAttachmentSocket: a local socket. It is the default.
	PortAttachmentSocket PortAttachment = "socket"
	// PortAttachmentWired: a cable or a bus. A forward cannot use it.
	PortAttachmentWired PortAttachment = "wired"
	// PortAttachmentWireless: a radio medium. A forward cannot use it.
	PortAttachmentWireless PortAttachment = "wireless"
)

// PortTransport is the transport of a port.
// +kubebuilder:validation:Enum=stream;datagram;frame
type PortTransport string

const (
	// PortTransportStream: a TCP byte stream. It is the default.
	PortTransportStream PortTransport = "stream"
	// PortTransportDatagram: UDP. A forward cannot use it.
	PortTransportDatagram PortTransport = "datagram"
	// PortTransportFrame: link-layer frames. A forward cannot use it.
	PortTransportFrame PortTransport = "frame"
)

// DevicePort is a named connection point that a driver instance of an
// exporter reports. It has no local address: the exporter configuration
// keeps the addresses.
type DevicePort struct {
	// Name of the port. It is a DNS label, unique in one exporter.
	// +kubebuilder:validation:MaxLength=63
	// +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
	Name string `json:"name"`
	// Direction of the port.
	Direction PortDirection `json:"direction"`
	// Protocol is a free-text protocol tag, for example "hci-h4". The
	// controller compares it only if the two ends of a forward set it.
	// +kubebuilder:validation:MaxLength=63
	Protocol string `json:"protocol,omitempty"`
	// Attachment of the port. No value means socket.
	Attachment PortAttachment `json:"attachment,omitempty"`
	// Transport of the port. No value means stream.
	Transport PortTransport `json:"transport,omitempty"`
}
