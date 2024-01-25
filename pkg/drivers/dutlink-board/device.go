package dutlink_board

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"go.bug.st/serial"
)

type JumpstarterDevice struct {
	driver         *DUTLinkDriver
	devicePath     string
	version        string
	serialNumber   string
	serialPort     serial.Port
	mutex          *sync.Mutex
	singletonMutex *sync.Mutex
	name           string
	storage_filter string
	json_config    map[string]string
	oobSerialPort  serial.Port
	usb_console    string
	tags           []string
	busy           bool
	consoleMode    bool
}

func (d *JumpstarterDevice) Lock() error {
	return d.ensureSerial()
}

func (d *JumpstarterDevice) Unlock() error {
	return d.closeSerial()
}

func (d *JumpstarterDevice) Power(action string) error {
	action = strings.ToLower(action)
	expected_response := "Device "

	if err := d.ensureSerial(); err != nil {
		return fmt.Errorf("Power(%q): %w", action, err)
	}

	if err := d.exitConsole(); err != nil {
		return fmt.Errorf("Power(%q): %w", action, err)
	}

	d.serialPort.SetReadTimeout(30 * time.Second)

	switch action {
	case "on":
		expected_response = "Device powered on"
	case "off":
		expected_response = "Device powered off"
	case "force-off":
		expected_response = "Device forced off"
	case "force-on":
		expected_response = "Device forced on"
	case "rescue":
		expected_response = "Device powered on to rescue"

	}

	if err := d.sendAndExpect_t("power "+action, expected_response, 30*time.Second); err != nil {
		return fmt.Errorf("Power(%q): %w", action, err)
	}

	return nil
}

func (d *JumpstarterDevice) Console() (harness.ConsoleInterface, error) {
	if d.usb_console == "" {
		return d.inBandConsole()
	}
	return d.outOfBandConsole()
}

func (d *JumpstarterDevice) SetConsoleSpeed(bps int) error {
	return harness.ErrNotImplemented
}

func (d *JumpstarterDevice) Driver() harness.HarnessDriver {
	return d.driver
}

func (d *JumpstarterDevice) Version() (string, error) {
	return d.version, nil
}

func (d *JumpstarterDevice) Serial() (string, error) {
	return d.serialNumber, nil
}

func (d *JumpstarterDevice) SetControl(signal string, value string) error {

	signal = strings.ToLower(signal)
	value = strings.ToLower(value)

	switch value {
	case "low":
		value = "l"
	case "high":
		value = "h"
	case "hiz":
		value = "z"
	}

	if signal == "reset" {
		signal = "r"
	}

	// check if is valid (r, a, b, c, or d)
	if !strings.Contains("rabcd", signal) {
		return fmt.Errorf("SetControl(%q,%q): invalid signal, must be any of reset|r|a|b|c|d", signal, value)
	}

	// check if value is valid (h, l or z)
	if !strings.Contains("hlz", value) {
		return fmt.Errorf("SetControl(%q,%q): invalid value, must be any of h|l|z|high|low|hiz", signal, value)
	}

	if err := d.ensureSerial(); err != nil {
		return fmt.Errorf("SetControl(%q,%q): %w", signal, value, err)
	}

	if err := d.exitConsole(); err != nil {
		return fmt.Errorf("SetControl(%q,%q): %w", signal, value, err)
	}

	setCmd := fmt.Sprintf("set %s %s", signal, value)

	if err := d.sendAndExpect(setCmd, setCmd+"\r\nSet "); err != nil {
		return fmt.Errorf("SetControl(%q,%q): %w", signal, value, err)
	}

	return nil
}

func (d *JumpstarterDevice) Device() (string, error) {
	return d.devicePath, nil
}
