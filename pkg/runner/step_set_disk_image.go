package runner

import (
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func (t *SetDiskImageStep) run(device harness.Device) StepResult {

	err := device.SetDiskImage(t.Image, uint64(t.OffsetGB)*1024*1024*1024)
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
