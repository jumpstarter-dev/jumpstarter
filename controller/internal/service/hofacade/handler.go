package hofacade

import (
	"encoding/json"
	"errors"
	"net/http"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/hofacade/howire"
)

// handlerFunc is the shape of every authenticated facade handler: it receives
// the request and the already-resolved caller Client CR and returns
// (result, error) under the upstream httpHandler contract
// (android-cuttlefish orchestrator/controller.go:145-181):
//
//	(nil, nil)  -> bare 200, empty body, no Content-Type
//	(obj, nil)  -> 200 + application/json
//	(nil, err)  -> AppError status + ErrorMsg body (500 fallback otherwise)
//
// All success statuses are 200 — never 201/202/204.
type handlerFunc func(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error)

// authed is the single authentication chokepoint: every data-serving route
// passes through it. Resolver failures produce a uniform 401 ErrorMsg and the
// wrapped handler is never invoked.
func (s *Server) authed(fn handlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		caller, err := s.Resolver.Resolve(r)
		if err != nil {
			writeError(w, err)
			return
		}
		res, err := fn(r, caller)
		dispatch(w, res, err)
	}
}

// unauthenticated wraps handlers that intentionally serve without a caller
// identity (GET /_debug/statusz, the 501 surface).
func unauthenticated(fn func(r *http.Request) (any, error)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		res, err := fn(r)
		dispatch(w, res, err)
	}
}

func dispatch(w http.ResponseWriter, res any, err error) {
	if err != nil {
		writeError(w, err)
		return
	}
	if res == nil {
		// Bare 200: upstream controller.go:158-161 (okHandler / nil result).
		w.WriteHeader(http.StatusOK)
		return
	}
	writeJSON(w, res, http.StatusOK)
}

// writeError mirrors upstream replyJSONErr (controller.go:168-174): a
// non-AppError becomes exactly
// 500 {"error":"Internal server error","details":"Internal server error: <err>"}.
func writeError(w http.ResponseWriter, err error) {
	var appErr *AppError
	if !errors.As(err, &appErr) {
		appErr = NewInternalError(howire.MsgInternal, err)
	}
	writeJSON(w, appErr.JSONResponse(), appErr.StatusCode)
}

func writeJSON(w http.ResponseWriter, obj any, statusCode int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	_ = json.NewEncoder(w).Encode(obj)
}

// notImplemented serves the out-of-scope HO surface: 501 with the upstream
// ErrorMsg body shape so real HO clients fail cleanly instead of choking on
// an unexpected body. Unauthenticated by design — it serves no data.
func notImplemented() http.HandlerFunc {
	return unauthenticated(func(r *http.Request) (any, error) {
		return nil, NewNotImplementedError("Not implemented")
	})
}
