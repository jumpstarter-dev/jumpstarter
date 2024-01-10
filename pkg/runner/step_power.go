package runner

import (
	"strings"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func (t *PowerStep) run(device harness.Device) StepResult {
	action := strings.ToLower(string(*t))
	switch action {
	case "cycle":
		err := device.Power("off")
		if err != nil {
			return StepResult{
				status: Fatal,
				err:    err,
			}
		}
		time.Sleep(2 * time.Second)
		err = device.Power("on")
		if err != nil {
			return StepResult{
				status: Fatal,
				err:    err,
			}
		}
	default:
		err := device.Power(action)
		if err != nil {
			return StepResult{
				status: Fatal,
				err:    err,
			}
		}
	}

	return StepResult{
		status: Done,
		err:    nil,
	}
}
