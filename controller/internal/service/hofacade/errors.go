package hofacade

import (
	"net/http"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/hofacade/howire"
)

// AppError mirrors the upstream Host Orchestrator error type
// (android-cuttlefish frontend/src/liboperator/operator/errors.go:23-62) so
// error responses are wire-identical to upstream: the HTTP status comes from
// StatusCode and the body is howire.ErrorMsg{error, details}.
type AppError struct {
	Msg        string
	StatusCode int
	Err        error
}

func (e *AppError) Error() string {
	if e.Err != nil {
		return e.Msg + ": " + e.Err.Error()
	}
	return e.Msg
}

func (e *AppError) Unwrap() error {
	return e.Err
}

// JSONResponse renders the wire body. details carries "Msg: cause" and is
// omitted entirely (omitempty) when there is no underlying cause.
func (e *AppError) JSONResponse() howire.ErrorMsg {
	msg := howire.ErrorMsg{Error: e.Msg}
	if e.Err != nil {
		msg.Details = e.Error()
	}
	return msg
}

// The constructors below mirror upstream errors.go:44-62 one-to-one.

func NewBadRequestError(msg string, err error) *AppError {
	return &AppError{Msg: msg, StatusCode: http.StatusBadRequest, Err: err}
}

func NewInternalError(msg string, err error) *AppError {
	return &AppError{Msg: msg, StatusCode: http.StatusInternalServerError, Err: err}
}

func NewNotFoundError(msg string, err error) *AppError {
	return &AppError{Msg: msg, StatusCode: http.StatusNotFound, Err: err}
}

func NewConflictError(msg string, err error) *AppError {
	return &AppError{Msg: msg, StatusCode: http.StatusConflict, Err: err}
}

func NewServiceUnavailableError(msg string, err error) *AppError {
	return &AppError{Msg: msg, StatusCode: http.StatusServiceUnavailable, Err: err}
}

// NewNotImplementedError is facade-local (upstream implements its whole
// surface, so it has no 501 constructor). Used for the deliberately
// out-of-scope HO endpoints so real HO clients fail cleanly on the familiar
// ErrorMsg body shape.
func NewNotImplementedError(msg string) *AppError {
	return &AppError{Msg: msg, StatusCode: http.StatusNotImplemented}
}

// NewUnauthorizedError is facade-local: upstream HO is unauthenticated, so
// 401 is a documented superset of the upstream contract. It never carries a
// cause — auth failures are uniform and leak nothing (no existence oracle).
func NewUnauthorizedError(msg string) *AppError {
	return &AppError{Msg: msg, StatusCode: http.StatusUnauthorized}
}
