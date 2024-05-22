package mock

import (
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

type MockDriver struct{}

func (d *MockDriver) Name() string {
	return "mock"
}

func (d *MockDriver) Description() string {
	return `Mock harness for testing.`
}

func (d *MockDriver) FindDevices() ([]harness.Device, error) {
	device, err := newMockDevice()
	if err != nil {
		return nil, err
	}
	hdList := []harness.Device{device}
	return hdList, nil
}
