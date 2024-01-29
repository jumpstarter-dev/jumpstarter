package runner

import "errors"

// basic errors
var ErrNoDevices = errors.New("no suitable devices were found")
var ErrAllDevicesBusy = errors.New("no available devices found, possible runners but busy")
var ErrAllDevicesBusyTimeout = errors.New("timed out waiting for devices to become available")
