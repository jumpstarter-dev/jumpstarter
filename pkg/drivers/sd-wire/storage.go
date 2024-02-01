package sdwire

import (
	"fmt"
	"os"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/pkg/storage"
	"github.com/jumpstarter-dev/jumpstarter/pkg/tools"
)

const BASE_DISKSBYID = "/dev/disk/by-id/"

const BLOCK_SIZE = 8 * 1024 * 1024

const WAIT_TIME_USB_STORAGE = 8 * time.Second

type StorageTarget int

const (
	HOST StorageTarget = iota
	DUT
)

func (d *SDWireDevice) SetDiskImage(path string, offset uint64) error {

	err := d.AttachStorage(false) // attach to host, detach from DUT
	if err != nil {
		return fmt.Errorf("SetDiskImage: %w", err)
	}
	for i := 0; i < 10; i++ {
		// check if path exists
		if _, err := os.Stat(d.storagePath); err == nil {
			break
		}
		time.Sleep(1 * time.Second)
	}

	if _, err := os.Stat(d.storagePath); err != nil {
		return fmt.Errorf("SetDiskImage: timeout waiting for device %q to come up", path)
	}

	fmt.Printf("📋 %s -> %s offset 0x%x: \n", path, d.storagePath, offset)

	if err := storage.WriteImageToDisk(path, d.storagePath, offset, BLOCK_SIZE, false); err != nil {
		return fmt.Errorf("SetDiskImage: %w", err)
	}

	return d.AttachStorage(true) // attach to DUT, detach from host
}

func (d *SDWireDevice) AttachStorage(connected bool) error {
	var err error
	switch connected {
	case true:
		err = tools.RunBash("sd-mux-ctrl --device-serial="+d.name+" --dut", os.Stdout, os.Stderr)
	case false:
		err = tools.RunBash("sd-mux-ctrl --device-serial="+d.name+" --ts", os.Stdout, os.Stderr)
	}
	if err != nil {
		return fmt.Errorf("AttachStorage(%v): %w", connected, err)
	}
	return nil
}
