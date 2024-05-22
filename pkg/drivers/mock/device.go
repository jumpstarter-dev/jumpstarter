package mock

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/jumpstarter-dev/jumpstarter/pkg/locking"
)

const JUMPSTARTER_MOCK_PATH = "/tmp/jumpstarter-mock.json"

type MockDeviceData struct {
	file         *os.File
	Name         string            `json:"name"`
	Device       string            `json:"device"`
	Version      string            `json:"version"`
	Serial       string            `json:"serial"`
	Tags         []string          `json:"tags"`
	Config       map[string]string `json:"config"`
	Control      map[string]string `json:"control"`
	Power        string            `json:"power"`
	ConsoleSpeed int               `json:"console_speed"`
	UsbConsole   string            `json:"usb_console"`
	ImagePath    string            `json:"image_path"`
	ImageOffset  uint64            `json:"image_offset"`
	Storage      bool              `json:"storage"`
	Busy         bool              `json:"busy"`
}

func (d *MockDeviceData) load() error {
	_, err := d.file.Seek(0, 0)
	if err != nil {
		return err
	}
	return json.NewDecoder(d.file).Decode(d)
}

func (d *MockDeviceData) save() error {
	_, err := d.file.Seek(0, 0)
	if err != nil {
		return err
	}
	return json.NewEncoder(d.file).Encode(d)
}

type MockDevice struct {
	driver   *MockDriver
	fileLock locking.Lock
	data     MockDeviceData
}

func newMockDevice() (*MockDevice, error) {
	file, err := os.OpenFile(JUMPSTARTER_MOCK_PATH, os.O_RDWR|os.O_CREATE, 0644)
	if err != nil {
		return nil, err
	}
	device := &MockDevice{
		driver: &MockDriver{},
		data: MockDeviceData{
			file:         file,
			Name:         "mock",
			Device:       "/dev/jumpstarter-mock",
			Version:      "1.0.0",
			Serial:       "001",
			Tags:         []string{},
			Config:       map[string]string{},
			Control:      map[string]string{},
			Power:        "off",
			ConsoleSpeed: 0,
			UsbConsole:   "",
			ImagePath:    "",
			ImageOffset:  0,
			Storage:      false,
			Busy:         false,
		},
	}
	device.data.load() // ignoring error
	err = device.data.save()
	if err != nil {
		return nil, err
	}
	return device, nil
}

func (d *MockDevice) Driver() harness.HarnessDriver {
	return d.driver
}

func (d *MockDevice) Version() (string, error) {
	err := d.data.load()
	return d.data.Version, err
}

func (d *MockDevice) Serial() (string, error) {
	err := d.data.load()
	return d.data.Serial, err
}

func (d *MockDevice) Name() string {
	d.data.load() // FIXME: check err
	return d.data.Name
}

func (d *MockDevice) SetName(name string) error {
	d.data.Name = name
	return d.data.save()
}

func (d *MockDevice) Tags() []string {
	d.data.load() // FIXME: check err
	return d.data.Tags
}

func (d *MockDevice) SetTags(tags []string) error {
	d.data.Tags = tags
	return d.data.save()
}

func (d *MockDevice) GetConfig() (map[string]string, error) {
	err := d.data.load()
	return d.data.Config, err
}

func (d *MockDevice) SetConfig(k, v string) error {
	d.data.Config[k] = v
	return d.data.save()
}

func (d *MockDevice) SetControl(key string, value string) error {
	d.data.Control[key] = value
	return d.data.save()
}

func (d *MockDevice) Power(action string) error {
	d.data.Power = action
	return d.data.save()
}

func (d *MockDevice) SetConsoleSpeed(bps int) error {
	d.data.ConsoleSpeed = bps
	return d.data.save()
}

func (d *MockDevice) SetUsbConsole(name string) error {
	d.data.UsbConsole = name
	return d.data.save()
}

func (d *MockDevice) SetDiskImage(path string, offset uint64) error {
	d.data.ImagePath = path
	d.data.ImageOffset = offset
	return d.data.save()
}

func (d *MockDevice) AttachStorage(connect bool) error {
	d.data.Storage = connect
	return d.data.save()
}

func (d *MockDevice) Device() (string, error) {
	err := d.data.load()
	return d.data.Device, err
}

func (d *MockDevice) IsBusy() (bool, error) {
	err := d.data.load()
	return d.data.Busy, err
}

func (d *MockDevice) Console() (harness.ConsoleInterface, error) {
	return newMockConsole()
}

func (d *MockDevice) Lock() error {
	err := d.data.load()
	if err != nil {
		return err
	}

	lock, err := locking.TryLock(d.data.Device)
	if err != nil {
		return fmt.Errorf("Lock: locking %q %w", d.data.Device, err)
	}

	d.fileLock = lock

	return nil
}

func (d *MockDevice) Unlock() error {
	return d.fileLock.Unlock()
}
