package jumpstarter_board

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	mapset "github.com/deckarep/golang-set/v2"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"go.bug.st/serial"
)

const PROMPT = "#> "
const ESCAPE_SEQUENCE = "\x02\x02\x02\x02\x02" // CTRL+B 5 times to exit from console to prompt (just in case)

func (d *JumpstarterDevice) ensureSerial() error {
	d.singletonMutex.Lock()
	defer d.singletonMutex.Unlock()
	if d.serialPort == nil {
		return d.openSerial()
	}
	return nil
}

func (d *JumpstarterDevice) closeSerial() error {
	d.mutex.Lock()
	defer d.mutex.Unlock()
	if d.serialPort != nil {
		err := d.serialPort.Close()
		d.serialPort = nil
		return err
	}
	return nil
}

func (d *JumpstarterDevice) openSerial() error {

	mode := &serial.Mode{
		BaudRate: 115200,
	}

	d.mutex.Lock()
	defer d.mutex.Unlock()

	if d.serialPort != nil {
		d.serialPort.Close()

	}

	port, err := serial.Open(d.devicePath, mode)
	if err != nil {
		return fmt.Errorf("openSerial: %w", err)
	}

	d.serialPort = port
	return nil
}

func (d *JumpstarterDevice) exitConsole() error {

	if !d.consoleMode {
		return nil
	}
	// make sure we are not in console mode
	if err := d.sendAndExpect(ESCAPE_SEQUENCE, "\n"); err != nil {
		return fmt.Errorf("exitConsole: %w", err)
	}

	// and disable monitor if enabled, as monitor will print any data received in the middle of the output
	if err := d.sendAndExpect("monitor off", "Monitor disabled"); err != nil {
		return fmt.Errorf("exitConsole: %w", err)
	}
	d.consoleMode = false
	return nil
}

func (d *JumpstarterDevice) sendAndExpect(cmd, expected string) error {
	d.mutex.Lock()
	defer d.mutex.Unlock()
	if err := d.send(cmd); err != nil {
		return fmt.Errorf("sendAndExpect(%q, %q) sending: %w", cmd, expected, err)
	}

	if err := d.expect(expected); err != nil {
		return fmt.Errorf("sendAndExpect(%q, %q) expecting response: %w", cmd, expected, err)
	}

	if err := d.expect(PROMPT); err != nil {
		return fmt.Errorf("sendAndExpect(%q, %q) waiting for prompt: %w", cmd, expected, err)
	}
	return nil
}

func (d *JumpstarterDevice) sendAndExpect_t(cmd, expected string, timeout time.Duration) error {
	d.mutex.Lock()
	defer d.mutex.Unlock()
	if err := d.send(cmd); err != nil {
		return fmt.Errorf("sendAndExpect(%q, %q) sending: %w", cmd, expected, err)
	}

	if err := d.expect_t(expected, timeout); err != nil {
		return fmt.Errorf("sendAndExpect(%q, %q) expecting response: %w", cmd, expected, err)
	}

	if err := d.expect(PROMPT); err != nil {
		return fmt.Errorf("sendAndExpect(%q, %q) waiting for prompt: %w", cmd, expected, err)
	}
	return nil
}

func (d *JumpstarterDevice) sendAndExpectNoPrompt(cmd, expected string) error {
	d.mutex.Lock()
	defer d.mutex.Unlock()
	if err := d.send(cmd); err != nil {
		return fmt.Errorf("sendAndExpect(%q, %q) sending: %w", cmd, expected, err)
	}

	if err := d.expect(expected); err != nil {
		return fmt.Errorf("sendAndExpect(%q, %q) expecting response: %w", cmd, expected, err)
	}

	return nil
}

func (d *JumpstarterDevice) send(cmd string) error {
	_, err := d.serialPort.Write([]byte(cmd + "\r\n"))
	if err != nil {
		return fmt.Errorf("sendCommand: %w", err)
	}

	return nil
}

func (d *JumpstarterDevice) expect(expected string) error {
	return d.expect_t(expected, 1*time.Second)
}

func (d *JumpstarterDevice) expect_t(expected string, timeout time.Duration) error {
	d.serialPort.SetReadTimeout(timeout)
	p := 0
	received := ""
	buf := make([]byte, 1)
	for p < len(expected) {
		n, err := d.serialPort.Read(buf)
		if err != nil {
			return fmt.Errorf("expect(%q): %w", expected, err)
		}
		if n == 0 {
			return fmt.Errorf("expect(%q): timeout, received=%q", expected, received)
		}
		c := buf[0]
		received += string(c)
		if c == expected[p] {
			p++
		} else {
			if c != expected[0] {
				p = 0
			} else {
				p = 1
			}
		}
	}
	return nil
}

func (d *JumpstarterDevice) inBandConsole() (harness.ConsoleInterface, error) {
	if err := d.ensureSerial(); err != nil {
		return nil, fmt.Errorf("Console: %w", err)
	}

	if d.consoleMode {
		return d.getConsoleWrapper(), nil
	}

	if err := d.exitConsole(); err != nil {
		return nil, fmt.Errorf("Console: %w", err)
	}

	if err := d.sendAndExpectNoPrompt("console", "Entering console mode, type CTRL+B 5 times to exit\r\n"); err != nil {
		return nil, fmt.Errorf("Console: %w", err)
	}

	d.consoleMode = true

	return d.getConsoleWrapper(), nil
}

func (d *JumpstarterDevice) outOfBandConsole() (harness.ConsoleInterface, error) {
	// TODO: in most cases the oob console would go away when we power off the device
	// so we need to add a wrapper to try reopening the port when it's gone away
	if d.oobSerialPort != nil {
		return d.oobSerialPort, nil
	}

	start := time.Now()
	max_wait_time := 15 * time.Second

	fmt.Fprintln(os.Stderr, "Looking up for out-of-band console: ", d.usb_console)

	for {
		devices, err := scanForSerialDevices(d.usb_console)
		if err != nil {
			return nil, fmt.Errorf("outOfBandConsole: %w", err)
		}
		if devices.Cardinality() > 1 {
			return nil, fmt.Errorf("outOfBandConsole: more than one device found: %v", devices)
		}
		if devices.Cardinality() == 1 {
			dev, _ := devices.Pop()
			mode := &serial.Mode{
				BaudRate: 115200,
			}
			if d.oobSerialPort != nil {
				d.oobSerialPort.Close()
			}
			var port serial.Port
			var err error

			// sometimes the device shows up and it is not ready yet, so we need to retry
			retries := 5
			for retries > 0 {
				port, err = serial.Open(dev, mode)
				if err == nil {
					break
				}
				retries -= 1
				time.Sleep(1 * time.Second)
			}

			if err != nil {
				return nil, fmt.Errorf("outOfBandConsole: openSerial: %w", err)
			}
			d.oobSerialPort = port
			return d.oobSerialPort, nil
		}

		if time.Since(start) > max_wait_time {
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	return nil, fmt.Errorf("outOfBandConsole: timeout waiting for serial device containing %s, "+
		"please note that out-of-band consoles usually require the device to be powered on", d.usb_console)
}

const BASE_SERIALSBYID = "/dev/serial/by-id/"

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

type JumpstarterConsoleWrapper struct {
	serialPort        serial.Port
	jumpstarterDevice *JumpstarterDevice
}

func (c *JumpstarterDevice) getConsoleWrapper() harness.ConsoleInterface {
	return &JumpstarterConsoleWrapper{
		serialPort:        c.serialPort,
		jumpstarterDevice: c,
	}
}

func (c *JumpstarterConsoleWrapper) Write(p []byte) (n int, err error) {
	if c.serialPort == nil {
		return 0, fmt.Errorf("JumpstarterConsoleWrapper: console has been closed")
	}
	return c.serialPort.Write(p)
}

func (c *JumpstarterConsoleWrapper) Read(p []byte) (n int, err error) {
	if c.serialPort == nil {
		return 0, fmt.Errorf("JumpstarterConsoleWrapper: console has been closed")
	}
	return c.serialPort.Read(p)
}

func (c *JumpstarterConsoleWrapper) Close() error {
	err := c.jumpstarterDevice.exitConsole()
	c.serialPort = nil
	return err
}

func (c *JumpstarterConsoleWrapper) SetReadTimeout(t time.Duration) error {
	if c.serialPort == nil {
		return fmt.Errorf("JumpstarterConsoleWrapper: console has been closed")
	}
	return c.serialPort.SetReadTimeout(t)
}
