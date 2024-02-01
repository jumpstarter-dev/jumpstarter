package sdwire

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/jumpstarter-dev/jumpstarter/pkg/console"
)

const (
	BASE_UDEVPATH = "/sys/bus/usb/devices/"
	USB_VID       = "0424"
	USB_PID       = "2640"
)

func scanUdev() ([]*SDWireDevice, error) {
	res := []*SDWireDevice{}

	err := filepath.Walk(BASE_UDEVPATH, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		if info.IsDir() && info.Name() == "devices" {
			return nil
		}

		if info.Mode()&os.ModeSymlink != 0 {
			idProduct, err := readUdevAttribute(path, "idProduct")
			if err != nil {
				if os.IsNotExist(err) {
					return nil
				}
				return err
			}

			idVendor, err := readUdevAttribute(path, "idVendor")
			if err != nil {
				return err
			}

			if idVendor != USB_VID || idProduct != USB_PID {
				return nil
			}

			usbRootDevice := getUsbRootDevice(path)

			// read serial from the ftdi device inside the hub
			serial, err := readUdevAttribute(path+"/"+usbRootDevice+".2", "serial")
			if err != nil {
				return err
			}

			// find the block device name
			bdDir := fmt.Sprintf("%s/%s.1/%s.1:1.0/host2/target2:0:0/2:0:0:0/block/", path, usbRootDevice, usbRootDevice)
			var bdDev string
			entries, err := os.ReadDir(bdDir)
			if err != nil {
				return fmt.Errorf("scanUdev: scanning for block files in sd-wire device %w", err)
			}

			for _, entry := range entries {
				if entry.IsDir() {
					bdDev = "/dev/" + entry.Name()
					break
				}
			}

			if bdDev == "" {
				return fmt.Errorf("scanUdev: no block device found in sd-wire device %s", serial)
			}

			cfg, err := ReadConfig(serial)

			if err != nil && !os.IsNotExist(err) {
				fmt.Printf("Warning: a sdwire device with serial %q was found but no config file was found: %v\n", serial, err)
				return nil
			}

			device, err := console.FindUSBSerialDevice(cfg.USBConsole)
			if errors.Is(err, console.ErrDeviceNotFound) {
				fmt.Printf("Warning: a sdwire device with serial %q cannot find serial interface matching: %v\n", cfg.USBConsole, err)
				return nil
			}
			res = append(res, &SDWireDevice{
				name:        serial,
				devicePath:  device,
				storagePath: bdDev,
				config:      cfg,
				driver:      &SDWireDriver{},
			})

		}
		return nil
	})

	if err != nil {
		return nil, fmt.Errorf("scanUdev: %w", err)
	}
	return res, nil
}

// TODO Refactor into udev utils module
func readUdevAttribute(path string, attribute string) (string, error) {
	value, err := os.ReadFile(filepath.Join(path, attribute))
	if err != nil {
		return "", err
	}
	valueStr := strings.TrimRight(string(value), "\r\n")
	return valueStr, nil
}

// TODO Refactor into udev utils module
// getUsbRootDevice
// converts something like /sys/bus/usb/devices/1-2.5/ into 1-2.5
func getUsbRootDevice(path string) string {
	parts := strings.Split(path, "/")
	if len(parts) == 0 {
		return ""
	}
	return parts[len(parts)-1]
}
