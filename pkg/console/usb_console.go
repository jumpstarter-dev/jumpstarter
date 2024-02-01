package console

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	mapset "github.com/deckarep/golang-set/v2"
	"go.bug.st/serial"
)

func OpenUSBSerial(device string) (serial.Port, error) {

	mode := &serial.Mode{
		BaudRate: 115200,
	}

	var port serial.Port
	var err error

	// sometimes the device shows up and it is not ready yet, so we need to retry
	retries := 5
	for retries > 0 {
		port, err = serial.Open(device, mode)
		if err == nil {
			break
		}
		retries -= 1
		time.Sleep(1 * time.Second)
	}

	if err != nil {
		return nil, fmt.Errorf("USBConsole: openSerial: %w", err)
	}
	return port, nil
}
func FindUSBSerial(device string) (serial.Port, error) {
	start := time.Now()
	max_wait_time := 15 * time.Second

	fmt.Fprintln(os.Stderr, "Looking up for usb console: ", device)

	for {
		devices, err := scanForSerialDevices(device)
		if err != nil {
			return nil, fmt.Errorf("USBConsole: %w", err)
		}
		if devices.Cardinality() > 1 {
			return nil, fmt.Errorf("USBConsole: more than one device found: %v", devices)
		}
		if devices.Cardinality() == 1 {
			dev, _ := devices.Pop()
			return OpenUSBSerial(dev)
		}

		if time.Since(start) > max_wait_time {
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	return nil, fmt.Errorf("outOfBandConsole: timeout waiting for serial device containing %s, "+
		"please note that out-of-band consoles usually require the device to be powered on", device)
}

const BASE_SERIALSBYID = "/dev/serial/by-id/"

var ErrDeviceNotFound = errors.New("device not found")

func FindUSBSerialDevice(substring string) (string, error) {

	devices, err := scanForSerialDevices(substring)
	if err != nil {
		return "", fmt.Errorf("USBConsole: %w", err)
	}
	if devices.Cardinality() > 1 {
		return "", fmt.Errorf("USBConsole: more than one device found: %v", devices)
	}
	if devices.Cardinality() == 1 {
		dev, _ := devices.Pop()
		return dev, nil
	}
	return "", fmt.Errorf("USBConsole: no device found containing %s, %w", substring, ErrDeviceNotFound)
}

func scanForSerialDevices(substring string) (mapset.Set[string], error) {

	interfaceSet := mapset.NewSet[string]()

	err := filepath.Walk(BASE_SERIALSBYID, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		if info.IsDir() && info.Name() == "devices" {
			return nil
		}

		if info.Mode()&os.ModeSymlink != 0 {
			baseName := filepath.Base(path)

			if strings.Contains(baseName, substring) {
				interfaceSet.Add(path)
			}
		}
		return nil
	})

	if err != nil {
		return nil, fmt.Errorf("scanForSerialDevices: %w", err)
	}

	return interfaceSet, nil
}
