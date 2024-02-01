package sdwire

import (
	"fmt"
	"os"
	"sync"

	"github.com/jumpstarter-dev/jumpstarter/pkg/console"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/jumpstarter-dev/jumpstarter/pkg/locking"
	"github.com/jumpstarter-dev/jumpstarter/pkg/tools"
	"go.bug.st/serial"
)

type SDWireDevice struct {
	driver         *SDWireDriver
	config         *SDWireConfig
	devicePath     string
	storagePath    string
	name           string
	serialPort     serial.Port
	fileLock       locking.Lock
	mutex          *sync.Mutex
	singletonMutex *sync.Mutex
	busy           bool
}

func (d *SDWireDevice) Lock() error {
	lock, err := locking.TryLock(d.devicePath)
	d.fileLock = lock
	if err != nil {
		return fmt.Errorf("Lock: locking %q %w", d.devicePath, err)
	}
	return nil
}

func (d *SDWireDevice) Unlock() error {
	return d.fileLock.Unlock()
}

func (d *SDWireDevice) Power(action string) error {
	if d.config.SmartPlug == nil || d.config.SmartPlug.Generic == nil {
		return fmt.Errorf("Power: no smart plug configured %+v", d.config)
	}

	if action == "on" || action == "force-on" {
		return tools.RunBash(d.config.SmartPlug.Generic.OnCommand, nil, os.Stderr)
	}

	if action == "off" || action == "force-off" {
		return tools.RunBash(d.config.SmartPlug.Generic.OffCommand, nil, os.Stderr)
	}

	return nil
}

func (d *SDWireDevice) Console() (harness.ConsoleInterface, error) {
	return console.OpenUSBSerial(d.devicePath)
}

func (d *SDWireDevice) SetConsoleSpeed(bps int) error {
	return harness.ErrNotImplemented
}

func (d *SDWireDevice) Driver() harness.HarnessDriver {
	return d.driver
}

func (d *SDWireDevice) Version() (string, error) {
	return "0.1", nil
}

func (d *SDWireDevice) Serial() (string, error) {
	return d.name, nil
}

func (d *SDWireDevice) SetControl(signal string, value string) error {

	return harness.ErrNotImplemented
}

func (d *SDWireDevice) Device() (string, error) {
	return d.devicePath, nil
}

func (d *SDWireDevice) GetConfig() (map[string]string, error) {
	config := map[string]string{}
	return config, nil
}

func (d *SDWireDevice) SetConfig(k, v string) error {
	return harness.ErrNotImplemented
}

func (d *SDWireDevice) SetName(name string) error {
	return harness.ErrNotImplemented
}

func (d *SDWireDevice) SetUsbConsole(usb_console string) error {
	return harness.ErrNotImplemented
}

func (d *SDWireDevice) SetTags(tags []string) error {
	return harness.ErrNotImplemented
}

func (d *SDWireDevice) Tags() []string {
	return d.config.Tags
}

func (d *SDWireDevice) IsBusy() (bool, error) {
	return d.busy, nil
}

func (d *SDWireDevice) Name() string {
	return d.name
}
