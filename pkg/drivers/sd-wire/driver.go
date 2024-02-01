package sdwire

import (
	"fmt"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

type SDWireDriver struct{}

func (d *SDWireDriver) Name() string {
	return "sd-wire-splug"
}

func (d *SDWireDriver) Description() string {
	return `SD-Wire + smart plug + usb-serial based driver
	enables the control of Edge and Embedded devices via smart plug and usb-serial,
	controlling storage via an Open Hardware sd-wire device.
	   https://shop.3mdeb.com/shop/open-source-hardware/sdwire/
	It has the following capabilities: power metering, power cycling, and serial console
	access, and USB storage switching.
	`
}

func (d *SDWireDriver) FindDevices() ([]harness.Device, error) {
	hdList := []harness.Device{}
	sdwires, err := scanUdev()
	if err != nil {
		return nil, fmt.Errorf("FindDevices: %w", err)
	}
	for _, jumpstarter := range sdwires {
		hdList = append(hdList, jumpstarter)
	}
	return hdList, nil
}

func init() {
	harness.RegisterDriver(&SDWireDriver{})
}
