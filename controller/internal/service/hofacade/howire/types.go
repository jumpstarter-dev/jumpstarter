// Package howire hand-mirrors the upstream Android Cuttlefish Host
// Orchestrator (HO) JSON wire types the ho-facade speaks.
//
// Per JEP-0016 DD-7 these are deliberate copies, NOT imports: the facade
// must not depend on libhoclient/hoapi Go modules (upstream's sibling-module
// pinning is fragile and drags the pion WebRTC stack into the supply chain).
// Each type cites the upstream source it mirrors; the referenced files are
// from github.com/google/android-cuttlefish @ main:
//
//   - frontend/src/host_orchestrator/orchestrator/api/v1/messages.go
//   - frontend/src/liboperator/api/v1/messages.go
//
// The wire-pin tests in types_test.go assert the exact JSON field names so
// any drift from upstream is caught byte-for-byte.
package howire

import "encoding/json"

// Canonical HO message strings, verbatim from upstream handler code
// (frontend/src/host_orchestrator/orchestrator/controller.go). The Python
// driver's retry/terminal logic keys off these exact strings and the
// associated status codes.
const (
	// controller.go:211 (createCVDHandler) et al.
	MsgMalformedJSON = "Malformed JSON in request"
	// controller.go:578,606,632 (operation handlers).
	MsgOperationNotFound = "Operation not found"
	// controller.go:608 (getOperationResultHandler).
	MsgOperationNotDone = "Operation not done"
	// controller.go:634 (waitOperationHandler): 503 = normal long-poll
	// expiry, the client retries.
	MsgWaitTimeout = "Wait for operation timed out"
	// controller.go replyJSONErr fallback for non-AppError errors.
	MsgInternal = "Internal server error"
)

// CVD status strings. Upstream populates CVD.Status verbatim from `cvd fleet`
// output — free-form cvd strings, not an API enum. These are the two values
// the facade renders.
const (
	StatusRunning  = "Running"
	StatusStarting = "Starting"
)

// Operation mirrors orchestrator/api/v1/messages.go:38-44.
// The name is a bare UUID string (upstream: uuid.New().String(),
// operation.go:88-110) with no "operations/" resource prefix, and there are
// NO embedded error/response fields (unlike GCP LROs): success or failure is
// only observable via /operations/{name}/result or /:wait.
type Operation struct {
	Name string `json:"name"`
	// If false the operation is still in progress; if true it is completed
	// and its result is available via /operations/{name}/result.
	Done bool `json:"done"`
}

// CVD mirrors orchestrator/api/v1/messages.go:46-61 exactly — including
// adb_port as a number and the absence of any build_source field.
type CVD struct {
	// [Output Only] The group name the instance belongs to.
	Group string `json:"group"`
	// [Output Only] Identifier within a group.
	Name string `json:"name"`
	// [Output Only]
	Status string `json:"status"`
	// [Output Only]
	Displays []string `json:"displays"`
	// [Output Only]
	WebRTCDeviceID string `json:"webrtc_device_id"`
	// [Output Only]
	ADBSerial string `json:"adb_serial"`
	// [Output Only]
	ADBPort uint32 `json:"adb_port"`
}

// CreateCVDRequest mirrors orchestrator/api/v1/messages.go:21-28. Upstream
// itself treats env_config as a black box ("its content is unstable"), and
// JEP-0016 F2 hardens that into a type-level guarantee: json.RawMessage means
// the facade cannot parse, validate, or reinterpret the interior even by
// accident.
type CreateCVDRequest struct {
	EnvConfig json.RawMessage `json:"env_config"`
}

// CreateCVDResponse mirrors orchestrator/api/v1/messages.go:31-33.
type CreateCVDResponse struct {
	CVDs []*CVD `json:"cvds"`
}

// ListCVDsResponse mirrors orchestrator/api/v1/messages.go:67-69.
type ListCVDsResponse struct {
	CVDs []*CVD `json:"cvds"`
}

// NewListCVDsResponse builds a ListCVDsResponse whose cvds field is never
// null on the wire: upstream may emit "cvds": null for a nil Go slice and the
// driver tolerates it (result.get('cvds', [])), but [] is strictly safer.
func NewListCVDsResponse(cvds []*CVD) *ListCVDsResponse {
	if cvds == nil {
		cvds = []*CVD{}
	}
	return &ListCVDsResponse{CVDs: cvds}
}

// ListOperationsResponse mirrors orchestrator/api/v1/messages.go:35-37.
// GET /operations lists only not-done operations (upstream MapOM.ListRunning,
// operation.go:112-122).
type ListOperationsResponse struct {
	Operations []Operation `json:"operations"`
}

// ErrorMsg mirrors liboperator/api/v1/messages.go:67-70: {"error"} plus an
// omitempty "details" and NO code field — the HTTP status code lives only in
// the status line.
type ErrorMsg struct {
	Error   string `json:"error"`
	Details string `json:"details,omitempty"`
}

// EmptyResponse mirrors orchestrator/api/v1/messages.go:109-111; it
// serializes as {} and is the completed result value of delete/stop-style
// operations (execcvdcommandaction.go:73-76).
type EmptyResponse struct{}
