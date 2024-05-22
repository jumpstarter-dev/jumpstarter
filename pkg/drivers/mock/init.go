//go:build mock

package mock

import (
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func init() {
	harness.RegisterDriver(&MockDriver{})
}
