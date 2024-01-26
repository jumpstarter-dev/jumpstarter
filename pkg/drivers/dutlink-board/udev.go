package dutlink_board

import (
	"fmt"
	"io/ioutil"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const (
	BASE_UDEVPATH = "/sys/bus/usb/devices/"
	USB_VID       = "2b23"
	USB_PID       = "1012"
)

/* we are looking for this info but without using udevadm
$ udevadm info --name=ttyACM0 | grep SERIAL
E: ID_SERIAL=Red_Hat_Inc._Jumpstarter_e6058905
E: ID_SERIAL_SHORT=e6058905
E: ID_USB_SERIAL=Red_Hat_Inc._Jumpstarter_e6058905
E: ID_USB_SERIAL_SHORT=e6058905

$ udevadm info --name=ttyACM0 | grep MODEL
E: ID_MODEL=Jumpstarter
E: ID_MODEL_ENC=Jumpstarter
E: ID_MODEL_ID=1012
E: ID_USB_MODEL=Jumpstarter
E: ID_USB_MODEL_ENC=Jumpstarter
E: ID_USB_MODEL_ID=1012

$ ls /sys/bus/usb/devices/1-2.5/
1-2.5:1.0   avoid_reset_quirk    bDeviceProtocol  bMaxPower           configuration  devpath  idProduct     maxchild  quirks     serial     uevent
1-2.5:1.1   bcdDevice            bDeviceSubClass  bNumConfigurations  descriptors    devspec  idVendor      port      removable  speed      urbnum
1-2.5:1.2   bConfigurationValue  bmAttributes     bNumInterfaces      dev            driver   ltm_capable   power     remove     subsystem  version
authorized  bDeviceClass         bMaxPacketSize0  busnum              devnum         ep_00    manufacturer  product   rx_lanes   tx_lanes

$ cat /sys/bus/usb/devices/1-2.5/idProduct
1012

$ cat /sys/bus/usb/devices/1-2.5/idVendor
2b23

$ cat /sys/bus/usb/devices/1-2.5/product
Jumpstarter

$ cat /sys/bus/usb/devices/1-2.5/bcdDevice
0004

$ cat /sys/bus/usb/devices/1-2.5/serial
e6058905

$ cat /sys/bus/usb/devices/1-2.5/1-2.5\:1.0/tty/ttyACM0/uevent
MAJOR=166
MINOR=0
DEVNAME=ttyACM0
*/

// write a function that scans the BASE_UDEVPATH for devices that match the
// vendor and product id of the DUTlink board using filepath.Walk and
// reading the right files based on the info above
//
// return a list of devices that match
// return an error if there is a problem
func scanUdev() ([]*JumpstarterDevice, error) {
	res := []*JumpstarterDevice{}

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

			if idVendor != "2b23" || idProduct != "1012" {
				return nil
			}

			version, err := readUdevAttribute(path, "bcdDevice")
			if err != nil {
				return err
			}

			// convert the version bcd string like 0004 to 0.04
			major, err := strconv.Atoi(version[0:2])
			if err != nil {
				return fmt.Errorf("scanUdev: error parsing bcdDevice %w", err)
			}
			version = fmt.Sprintf("%d.%s", major, version[2:])

			serial, err := readUdevAttribute(path, "serial")
			if err != nil {
				return err
			}

			usbRootDevice := getUsbRootDevice(path)
			ttyDir := filepath.Join(path, usbRootDevice+":1.0", "tty")
			ttynames, err := ioutil.ReadDir(ttyDir)
			if err != nil {
				return err
			}

			if len(ttynames) != 1 {
				panic("expected only one tty device")
			}

			jp, err := newJumpstarter(ttynames[0].Name(), version, serial)
			if err != nil {
				return err
			}
			res = append(res, &jp)

		}
		return nil
	})

	if err != nil {
		return nil, fmt.Errorf("scanUdev: %w", err)
	}
	return res, nil
}

func readUdevAttribute(path string, attribute string) (string, error) {
	value, err := ioutil.ReadFile(filepath.Join(path, attribute))
	if err != nil {
		return "", err
	}
	valueStr := strings.TrimRight(string(value), "\r\n")
	return valueStr, nil
}

// getUsbRootDevice
// converts something like /sys/bus/usb/devices/1-2.5/ into 1-2.5
func getUsbRootDevice(path string) string {
	parts := strings.Split(path, "/")
	if len(parts) == 0 {
		return ""
	}
	return parts[len(parts)-1]
}
