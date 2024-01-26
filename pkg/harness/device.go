package harness

import (
	"io"
	"time"
)

type ConsoleInterface interface {
	io.ReadWriteCloser
	SetReadTimeout(t time.Duration) error
}

type Device interface {
	Driver() HarnessDriver
	Power(action string) error
	Console() (ConsoleInterface, error)
	SetConsoleSpeed(bps int) error
	Version() (string, error)
	Name() string                    // name of the device, can be assigned by the user
	Tags() []string                  // tags assigned to the device, can be assigned by the user
	SetName(name string) error       // set the name of the device, should be stored in config or flashed to device
	SetUsbConsole(name string) error // set the substring of an out of band console name for this device
	SetTags(tags []string) error
	SetConfig(k, v string) error
	GetConfig() (map[string]string, error)
	Serial() (string, error)
	SetDiskImage(path string, offset uint64) error
	AttachStorage(connect bool) error
	SetControl(key string, value string) error
	Device() (string, error)
	IsBusy() (bool, error)
	Lock() error   // open/lock the device so other instances cannot use it
	Unlock() error // close the locked device so other instances can use it
}
