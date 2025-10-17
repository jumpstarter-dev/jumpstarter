package log

// Log levels for use with controller-runtime's logr.Logger.V() method.
// Higher numbers mean more verbose logging.
const (
	// LevelInfo is the standard info level (V(0))
	LevelInfo = 0

	// LevelDebug is for debug-level logging (V(1))
	// Use for detailed operational information that is useful for debugging
	// but not needed during normal operation.
	LevelDebug = 1

	// LevelTrace is for trace-level logging (V(2))
	// Use for very detailed information about internal operations,
	// useful for troubleshooting complex issues.
	LevelTrace = 2
)
