package runner

import (
	"fmt"

	"github.com/fatih/color"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func (t *CommentStep) run(device harness.Device) StepResult {

	color.Set(color.FgHiYellow)
	fmt.Printf("\n➤ %s\n", string(*t))
	color.Unset()
	return StepResult{
		status: SilentOk,
		err:    nil,
	}
}
