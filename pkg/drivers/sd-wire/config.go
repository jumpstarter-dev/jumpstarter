package sdwire

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// yaml parser

// see etc/jumpstarter/sd-wire/device0.yaml example file
type SDWireConfig struct {
	Serial     string           `yaml:"serial"`
	Tags       []string         `yaml:"tags"`
	USBConsole string           `yaml:"usb_console"`
	SmartPlug  *SmartPlugConfig `yaml:"smartplug"`
}

type SmartPlugConfig struct {
	Generic *SmartPlugGenericConfig `yaml:"generic"`
}

type SmartPlugGenericConfig struct {
	OnCommand  string `yaml:"on_command"`
	OffCommand string `yaml:"off_command"`
}

const CONFIG_BASE_PATH = "/etc/jumpstarter/sd-wire"

func ReadConfig(serial string) (*SDWireConfig, error) {
	yaml_file := CONFIG_BASE_PATH + "/" + serial + ".yaml"
	script_data, err := os.ReadFile(yaml_file)
	if err != nil {
		return nil, fmt.Errorf("ReadConfig(%q): Error reading yaml file: %w", yaml_file, err)
	}
	config := SDWireConfig{}
	if err := yaml.Unmarshal([]byte(script_data), &config); err != nil {
		return nil, fmt.Errorf("ReadConfig(%q): %w", yaml_file, err)
	}

	// TODO: Apply sanity checks to configuration
	return &config, nil
}
