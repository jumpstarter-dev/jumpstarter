package harness

import "errors"

// basic errors
var ErrDeviceInUse = errors.New("device is in use")
var ErrCannotOpenDevice = errors.New("unable to open device tty")
var ErrNotImplemented = errors.New("not implemented")
var ErrCannotSetBaudRate = errors.New("unable to set baud rate")
var ErrCannotSetDiskImage = errors.New("unable to set disk image")
var ErrDeviceNotResponding = errors.New("device not responding")
