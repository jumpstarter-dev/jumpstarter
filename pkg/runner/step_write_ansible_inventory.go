package runner

import (
	"fmt"
	"os"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/jumpstarter-dev/jumpstarter/pkg/tools"
)

func (t *WriteAnsibleInventoryStep) run(device harness.Device) StepResult {

	// Open the inventory file t.Filename for writing
	// If the file doesn't exist, create it or append to the file
	inventoryFile, err := os.OpenFile(t.Filename, os.O_RDWR|os.O_CREATE|os.O_TRUNC, 0644)
	if err != nil {
		return StepResult{
			status: Fatal,
			err:    fmt.Errorf("WriteAnsibleInventory:run(%q) opening file for write: %w", t.Filename, err),
		}
	}
	defer inventoryFile.Close()

	if err := tools.CreateAnsibleInventory(device, inventoryFile, t.User, t.SshKey); err != nil {
		return StepResult{
			status: Fatal,
			err:    fmt.Errorf("WriteAnsibleInventory:run(%q) writing inventory file: %w", t.Filename, err),
		}
	}

	color.Set(color.FgYellow)
	fmt.Println("\n\rwritten :", t.Filename)
	color.Unset()

	fmt.Println("")

	return StepResult{
		status: SilentOk,
		err:    nil,
	}
}
