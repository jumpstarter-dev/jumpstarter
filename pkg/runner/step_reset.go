package runner

import (
	"fmt"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func (t *ResetStep) run(device harness.Device) StepResult {

	fmt.Println("Resetting device...")
	err := device.SetControl("r", "l")
	if err != nil {
		return StepResult{
			status: Fatal,
			err:    err,
		}
	}

	ms := t.TimeMs
	if ms < 1000 {
		ms = 1000
	}

	time.Sleep(time.Duration(ms) * time.Millisecond)

	err = device.SetControl("r", "z")
	if err != nil {
		return StepResult{
			status: Fatal,
			err:    err,
		}
	}

	return StepResult{
		status: Done,
		err:    nil,
	}
}
