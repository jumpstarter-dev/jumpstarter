package jumpstarter_board

import (
	"fmt"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

type JumpstarterDriver struct{}

func (d *JumpstarterDriver) Name() string {
	return "jumpstarter-board"
}

func (d *JumpstarterDriver) Description() string {
	return `OpenSource HIL USB harness (https://github.com/jumpstarter-dev/jumpstarter-board)
	enables the control of Edge and Embedded devices via USB.
	It has the following capabilities: power metering, power cycling, and serial console
	access, and USB storage switching.
	`
}

func (d *JumpstarterDriver) FindDevices() ([]harness.Device, error) {
	hdList := []harness.Device{}
	jumpstarters, err := scanUdev()
	if err != nil {
		return nil, fmt.Errorf("FindDevices: %w", err)
	}
	for _, jumpstarter := range jumpstarters {
		hdList = append(hdList, jumpstarter)
	}
	return hdList, nil
}

func init() {
	harness.RegisterDriver(&JumpstarterDriver{})
}
