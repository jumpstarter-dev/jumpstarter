package dutlink_board

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func newJumpstarter(ttyname string, version string, serial string) (JumpstarterDevice, error) {
	jp := JumpstarterDevice{
		devicePath:     "/dev/" + ttyname,
		version:        version,
		serialNumber:   serial,
		mutex:          &sync.Mutex{},
		singletonMutex: &sync.Mutex{},
		busy:           false,
		name:           "",
		json_config:    map[string]string{},
		storage_filter: "",
		tags:           []string{},
		consoleMode:    true, // let's asume it's in console mode so we will force exit at start
	}
	err := jp.readConfig()

	if err == nil || errors.Is(err, harness.ErrDeviceInUse) {
		return jp, nil
	} else {
		return JumpstarterDevice{}, err
	}
}

func (d *JumpstarterDevice) IsBusy() (bool, error) {
	return d.busy, nil
}

func (d *JumpstarterDevice) GetConfig() (map[string]string, error) {
	config := map[string]string{}

	buf, err := d.getConfigLines()
	if err != nil {
		return config, fmt.Errorf("readConfig: %w", err)
	}
	lines := strings.Split(buf, "\r\n")
	for _, line := range lines {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		k := strings.Trim(parts[0], " ")
		v := strings.Trim(parts[1], " ")
		config[k] = v
	}
	return config, nil
}

func (d *JumpstarterDevice) getConfigLines() (string, error) {
	err := d.ensureSerial()
	if errors.Is(err, harness.ErrDeviceInUse) {
		d.name = "**BUSY**"
		d.busy = true
		return "", nil
	}

	if err != nil {
		return "", fmt.Errorf("getConfigLines: %w", err)
	}

	defer d.closeSerial()

	if err := d.exitConsole(); err != nil {
		return "", fmt.Errorf("getConfigLines: %w", err)
	}

	if err := d.sendAndExpectNoPrompt("get-config", "get-config\r\n"); err != nil {
		return "", fmt.Errorf("getConfigLines: %w", err)
	}
	d.serialPort.SetReadTimeout(100 * time.Millisecond)

	buf := make([]byte, 1024)
	c := make([]byte, 1)
	p := 0
	// keep reading until we reach the prompt or the read times out
	for p < len(buf) {
		n, err := d.serialPort.Read(c)
		if err != nil {
			return "", fmt.Errorf("getConfigLines: %w", err)
		}
		if n == 0 || c[0] == '#' {
			break
		}
		buf[p] = c[0]
		p += 1
	}

	buf = buf[:p]
	return string(buf), nil
}
func (d *JumpstarterDevice) readConfig() error {

	buf, err := d.getConfigLines()
	if err != nil {
		return fmt.Errorf("readConfig: %w", err)
	}
	lines := strings.Split(buf, "\r\n")
	for _, line := range lines {
		if strings.HasPrefix(line, "name:") {
			d.name = strings.ToLower(strings.TrimSpace(strings.TrimPrefix(line, "name:")))
		}
		if strings.HasPrefix(line, "tags:") {
			d.tags = strings.Split(strings.ToLower(strings.TrimSpace(strings.TrimPrefix(line, "tags:"))), ",")
		}
		if strings.HasPrefix(line, "json:") {
			json_str := strings.TrimSpace(strings.TrimPrefix(line, "json:"))
			if json_str != "" {
				// umarshall json into a map[string] string
				var objmap map[string]string
				err := json.Unmarshal([]byte(json_str), &objmap)
				if err != nil {
					return fmt.Errorf("readConfig: json unmarshal %w", err)
				}
				d.json_config = objmap
				if storage_filter, ok := objmap["storage_filter"]; ok {
					d.storage_filter = storage_filter
				}
			}

		}
		if strings.HasPrefix(line, "usb_console:") {
			d.usb_console = strings.TrimSpace(strings.TrimPrefix(line, "usb_console:"))
		}
	}
	if d.name == "" {
		d.name = "jp-" + d.serialNumber
	}
	return nil
}

func (d *JumpstarterDevice) setConfig(k, v string) error {
	k = strings.ToLower(k)
	if strings.Contains(v, " ") || strings.Contains(v, "\r") || strings.Contains(v, "\n") {
		return fmt.Errorf("SetConfig(%v, %v): invalid value, cannot contain spaces or \\r \\n", k, v)
	}
	if err := d.ensureSerial(); err != nil {
		return fmt.Errorf("SetConfig(%v, %v): %w", k, v, err)
	}

	if err := d.exitConsole(); err != nil {
		return fmt.Errorf("SetConfig(%v, %v): %w", k, v, err)
	}

	if err := d.sendAndExpect("set-config "+k+" "+v, "Set "+k+" to "+v); err != nil {
		return fmt.Errorf("SetConfig(%v, %v) %w", k, v, err)
	}

	return nil
}

func (d *JumpstarterDevice) SetConfig(k, v string) error {
	// for parameters that are directly stored in the config list
	if k == "name" || k == "tags" || k == "usb_console" ||
		k == "power_on" || k == "power_off" || k == "power_rescue" {
		return d.setConfig(k, v)
	}
	// for parameters that we mash in the json field
	if k == "storage_filter" {
		if err := d.readConfig(); err != nil {
			return fmt.Errorf("SetConfig(%v, %v): %w", k, v, err)
		}
		d.json_config[k] = v
		json_str, err := json.Marshal(d.json_config)
		if err != nil {
			return fmt.Errorf("SetConfig(%v, %v): json.Marshal %w", k, v, err)
		}
		return d.setConfig("json", string(json_str))
	}
	return nil
}

func (d *JumpstarterDevice) SetName(name string) error {
	if err := d.ensureSerial(); err != nil {
		return fmt.Errorf("SetName(%v): %w", name, err)
	}

	if err := d.exitConsole(); err != nil {
		return fmt.Errorf("SetName(%v): %w", name, err)
	}

	if err := d.sendAndExpect("set-config name "+name, "Set name to "+name); err != nil {
		return fmt.Errorf("SetName(%v) %w", name, err)
	}

	return nil
}

func (d *JumpstarterDevice) SetUsbConsole(console_substring string) error {
	if err := d.ensureSerial(); err != nil {
		return fmt.Errorf("SetUsbConsole(%v): %w", console_substring, err)
	}

	if err := d.exitConsole(); err != nil {
		return fmt.Errorf("SetUsbConsole(%v): %w", console_substring, err)
	}

	if err := d.sendAndExpect("set-config usb_console "+console_substring, "Set usb_console to "+console_substring); err != nil {
		return fmt.Errorf("SetUsbConsole(%v) %w", console_substring, err)
	}

	return nil
}
func (d *JumpstarterDevice) Name() string {
	return d.name
}

func (d *JumpstarterDevice) SetTags(tags []string) error {
	joinTags := strings.Join(tags, ",")
	if err := d.ensureSerial(); err != nil {
		return fmt.Errorf("SetTags(%s): %w", joinTags, err)
	}

	if err := d.exitConsole(); err != nil {
		return fmt.Errorf("SetTags(%s): %w", joinTags, err)
	}

	if err := d.sendAndExpect("set-config tags "+strings.Join(tags, ","), "Set tags to "+joinTags); err != nil {
		return fmt.Errorf("SetTags(%s) %w", joinTags, err)
	}

	return nil
}

func (d *JumpstarterDevice) Tags() []string {
	return d.tags
}
