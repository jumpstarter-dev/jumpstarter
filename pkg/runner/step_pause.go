package runner

import (
	"time"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func (t *PauseStep) run(device harness.Device) StepResult {
	s := *t
	if s < 1 {
		s = 1
	}
	time.Sleep(time.Duration(s) * time.Second)

	return StepResult{
		status: Done,
		err:    nil,
	}
}
