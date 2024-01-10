package runner

import (
	"os"
	"os/exec"
	"strings"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func (t *LocalShellStep) run(device harness.Device) StepResult {
	/*
		// create a temporary file in /tmp
		file, err := os.CreateTemp("/tmp", "jumpstarter-*-localshell.sh")
		if err != nil {
			return TaskResult{
				status: Fatal,
				err:    err,
			}
		}
		file.WriteString(t.Script)
		file.Close()

		defer os.Remove(file.Name())

		// run the script
		cmd := "bash " + file.Name()
		// execute in system

	*/

	cmd := exec.Command("bash")
	cmd.Stdin = strings.NewReader("set -x\n" + t.Script)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stdout
	err := cmd.Run()
	if err != nil {
		return StepResult{
			status: Fatal,
			err:    err,
		}
	}

	return StepResult{
		status: SilentOk,
		err:    nil,
	}

}
