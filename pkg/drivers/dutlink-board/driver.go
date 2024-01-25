package dutlink_board

import (
	"fmt"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

type DUTLinkDriver struct{}

func (d *DUTLinkDriver) Name() string {
	return "dutlink-board"
}

func (d *DUTLinkDriver) Description() string {
	return `OpenSource HIL USB harness (https://github.com/jumpstarter-dev/dutlink-board)
	enables the control of Edge and Embedded devices via USB.
	It has the following capabilities: power metering, power cycling, and serial console
	access, and USB storage switching.
	`
}

func (d *DUTLinkDriver) FindDevices() ([]harness.Device, error) {
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
	harness.RegisterDriver(&DUTLinkDriver{})
}
